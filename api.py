"""
JSON-API для not_a_reddit. Префикс /api/v1.

Аутентификация: Authorization: Bearer <токен>
Токен юзер берёт у себя в профиле, кнопка "Сгенерировать токен API".

Для GET-эндпоинтов токен необязателен (публичные данные).
Для POST — обязателен.
"""
from flask import Blueprint, request, jsonify, abort
from functools import wraps
import secrets

from models import db, User, Post, Like, Comment, Category, Tag

api = Blueprint("api", __name__, url_prefix="/api/v1")


def _user_from_token():
    """достаёт юзера по заголовку Authorization, возвращает User или None"""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:].strip()
    if not token:
        return None
    return User.query.filter_by(api_token=token).first()


def api_auth(f):
    """требует валидный токен"""
    @wraps(f)
    def wrapper(*args, **kwargs):
        user = _user_from_token()
        if not user:
            return jsonify({"error": "нужен токен в Authorization: Bearer <token>"}), 401
        # пробрасываем юзера в kwargs чтоб не лезть в context каждый раз
        return f(*args, user=user, **kwargs)
    return wrapper


def generate_token():
    """случайный 32-байтный hex-токен"""
    return secrets.token_hex(32)


# === сериализация ===

def post_to_dict(p, viewer=None):
    return {
        "id": p.id,
        "title": p.title,
        "text": p.text,
        "image": p.image,
        "author": {"id": p.author_id, "nickname": p.author.nickname if p.author else None},
        "category": {"id": p.category_id, "name": p.category.name if p.category else None},
        "tags": [t.name for t in p.tags],
        "created_at": p.post_date.isoformat() if p.post_date else None,
        "edited_at": p.edited_at.isoformat() if p.edited_at else None,
        "likes": p.likes_count(),
        "comments": p.comments_count(),
        "views": p.views,
        "liked_by_me": p.liked_by(viewer) if viewer else False,
    }


def comment_to_dict(c):
    return {
        "id": c.id,
        "text": c.text,
        "author": {"id": c.author_id, "nickname": c.author.nickname},
        "parent_id": c.parent_id,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


# === роуты ===

@api.route("/posts", methods=["GET"])
def list_posts():
    # фильтры через query-параметры: ?cat=1&tag=python&q=поиск&limit=20&offset=0
    q = Post.query

    cat = request.args.get("cat", type=int)
    if cat:
        q = q.filter(Post.category_id == cat)

    tag = (request.args.get("tag") or "").strip().lstrip("#").lower()
    if tag:
        q = q.join(Post.tags).filter(Tag.name == tag)

    search = (request.args.get("q") or "").strip()
    if search:
        like = f"%{search}%"
        q = q.filter(db.or_(Post.title.ilike(like), Post.text.ilike(like))).distinct()

    # пагинация
    limit = min(request.args.get("limit", 20, type=int), 100)
    offset = max(request.args.get("offset", 0, type=int), 0)

    total = q.count()
    posts = q.order_by(Post.post_date.desc()).offset(offset).limit(limit).all()

    viewer = _user_from_token()
    return jsonify({
        "total": total,
        "limit": limit,
        "offset": offset,
        "posts": [post_to_dict(p, viewer) for p in posts],
    })


@api.route("/posts/<int:post_id>", methods=["GET"])
def get_post(post_id):
    p = Post.query.get_or_404(post_id)
    viewer = _user_from_token()
    data = post_to_dict(p, viewer)
    # к посту прикладываем плоский список всех комментов с parent_id для построения дерева
    data["comments_list"] = [comment_to_dict(c) for c in p.comments.all()]
    return jsonify(data)


@api.route("/categories", methods=["GET"])
def list_categories():
    cats = Category.query.order_by(Category.sort_order, Category.id).all()
    return jsonify({
        "categories": [{"id": c.id, "name": c.name} for c in cats]
    })


@api.route("/posts", methods=["POST"])
@api_auth
def create_post(user):
    # ждём json: {title, text, category_id, tags?}
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    text = (data.get("text") or "").strip()
    cat_id = data.get("category_id")
    tags_input = data.get("tags") or []

    if not title or not text:
        return jsonify({"error": "title и text обязательны"}), 400
    if len(title) > 200:
        return jsonify({"error": "title слишком длинный"}), 400

    cat = Category.query.get(cat_id) if cat_id else None
    if not cat:
        cat = Category.query.filter_by(name="Разное").first()
    if not cat:
        return jsonify({"error": "нет категории"}), 500

    p = Post(
        title=title, text=text,
        author_id=user.id, category_id=cat.id,
    )
    db.session.add(p)
    db.session.flush()

    # теги — принимаем как список или как строку
    from main import parse_tags, attach_tags_to_post
    if isinstance(tags_input, str):
        tag_names = parse_tags(tags_input)
    else:
        tag_names = parse_tags(" ".join(str(t) for t in tags_input))
    attach_tags_to_post(p, tag_names)

    db.session.commit()
    return jsonify(post_to_dict(p, user)), 201


@api.route("/posts/<int:post_id>/like", methods=["POST"])
@api_auth
def like_post(user, post_id):
    p = Post.query.get_or_404(post_id)
    existing = Like.query.filter_by(user_id=user.id, post_id=p.id).first()
    if existing:
        db.session.delete(existing)
        liked = False
    else:
        db.session.add(Like(user_id=user.id, post_id=p.id))
        liked = True
    db.session.commit()
    return jsonify({"liked": liked, "count": p.likes_count()})


@api.route("/posts/<int:post_id>/comment", methods=["POST"])
@api_auth
def comment_post(user, post_id):
    p = Post.query.get_or_404(post_id)
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    parent_id = data.get("parent_id")

    if not text:
        return jsonify({"error": "text обязателен"}), 400
    if len(text) > 1000:
        return jsonify({"error": "слишком длинный"}), 400

    if parent_id:
        parent = Comment.query.filter_by(id=parent_id, post_id=p.id).first()
        if not parent:
            return jsonify({"error": "родительский коммент не найден"}), 400
        # схлопываем глубину как и в обычном add_comment
        depth = 0
        cur = parent
        while cur.parent_id:
            depth += 1
            cur = cur.parent
        if depth >= 2:
            parent_id = parent.parent_id or parent.id

    c = Comment(text=text, author_id=user.id, post_id=p.id, parent_id=parent_id)
    db.session.add(c)
    db.session.commit()
    return jsonify(comment_to_dict(c)), 201


@api.route("/me", methods=["GET"])
@api_auth
def whoami(user):
    """проверка что токен работает + кто я"""
    return jsonify({
        "id": user.id,
        "nickname": user.nickname,
        "mail": user.mail,
        "is_admin": user.is_admin,
    })

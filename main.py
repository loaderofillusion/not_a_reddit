from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, abort, make_response, jsonify,
)
from flask_login import (
    LoginManager,
    login_user,
    logout_user,
    login_required,
    current_user,
)
from datetime import datetime
import os
import re
import uuid

from models import db, User, Post, Like, Comment, Follow, Reaction, Category, Tag, ALLOWED_REACTIONS
from functools import wraps

# Pillow юзаем для ресайза, без него тоже работает — просто без ресайза
try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "db.db")

UPLOAD_DIR = os.path.join(BASE_DIR, "static", "uploads")
AVATAR_DIR = os.path.join(UPLOAD_DIR, "avatars")
POST_IMG_DIR = os.path.join(UPLOAD_DIR, "posts")
os.makedirs(AVATAR_DIR, exist_ok=True)
os.makedirs(POST_IMG_DIR, exist_ok=True)

ALLOWED_EXT = {"png", "jpg", "jpeg", "gif", "webp"}
MAX_IMAGE_SIZE = 5 * 1024 * 1024
# не больше 3 реакций от одного юзера на один коммент. Новая выкидывает самую старую
MAX_REACTIONS_PER_USER = 3
# и не больше 5 тегов на пост
MAX_TAGS_PER_POST = 5

app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "very-secret-key")
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_PATH}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = MAX_IMAGE_SIZE

db.init_app(app)

# регистрируем JSON-API (см. api.py)
from api import api as api_bp
app.register_blueprint(api_bp)

login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Войди, чтобы продолжить"
login_manager.login_message_category = "warning"


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# чтобы тема и список реакций были доступны во всех шаблонах без отдельной передачи
@app.context_processor
def inject_globals():
    theme = request.cookies.get("theme", "light")
    # категории нужны почти везде (в шапке, на главной, в формах) — отдаём списком
    categories = Category.query.order_by(Category.sort_order, Category.id).all()
    return {
        "theme": theme,
        "ALLOWED_REACTIONS": ALLOWED_REACTIONS,
        "ALL_CATEGORIES": categories,
    }


def admin_required(f):
    """пропускает только админов, иначе 403"""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return wrapper


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXT


def save_image(file_storage, target_dir, max_side=1200):
    # сохраняет картинку под рандомным именем, большие ужимает
    if not file_storage or not file_storage.filename:
        return None
    if not allowed_file(file_storage.filename):
        return None

    ext = file_storage.filename.rsplit(".", 1)[1].lower()
    name = f"{uuid.uuid4().hex}.{ext}"
    path = os.path.join(target_dir, name)
    file_storage.save(path)

    # gif-ки не трогаем чтоб анимация не сломалась
    if HAS_PIL and ext != "gif":
        try:
            img = Image.open(path)
            if max(img.size) > max_side:
                img.thumbnail((max_side, max_side))
                img.save(path)
        except Exception:
            pass  # не получилось — да и ладно, оригинал на месте

    return name


def delete_image(filename, folder):
    if not filename:
        return
    path = os.path.join(folder, filename)
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


# принимает сырую строку из формы вида "python flask #учеба, #help"
# возвращает чистый список нормализованных тегов без дублей, не больше MAX_TAGS_PER_POST
_tag_clean_re = re.compile(r"[^\w\d]+", re.UNICODE)


def parse_tags(raw):
    if not raw:
        return []
    # делим по пробелам/запятым/новым строкам, убираем #
    parts = re.split(r"[\s,]+", raw.strip())
    result = []
    seen = set()
    for p in parts:
        p = p.lstrip("#").lower()
        # выкидываем мусорные символы, оставляя буквы/цифры/_
        p = _tag_clean_re.sub("_", p).strip("_")
        if not p or len(p) > 40:
            continue
        if p in seen:
            continue
        seen.add(p)
        result.append(p)
        if len(result) >= MAX_TAGS_PER_POST:
            break
    return result


def attach_tags_to_post(post, tag_names):
    """применяет список имён тегов к посту: создаёт новые если нужно, очищает старые"""
    new_tags = []
    for name in tag_names:
        t = Tag.query.filter_by(name=name).first()
        if not t:
            t = Tag(name=name)
            db.session.add(t)
            db.session.flush()  # чтоб id появился
        new_tags.append(t)
    post.tags = new_tags


@app.route("/")
def main_page():
    sort = request.args.get("sort", "new")
    feed = request.args.get("feed", "all")
    cat_id = request.args.get("cat", type=int)
    tag = (request.args.get("tag") or "").strip().lstrip("#").lower()
    search = (request.args.get("q") or "").strip()
    q = Post.query

    # фильтр по категории если выбрана
    if cat_id:
        q = q.filter(Post.category_id == cat_id)

    # фильтр по конкретному тегу (точное совпадение по имени)
    if tag:
        q = q.join(Post.tags).filter(Tag.name == tag)

    # поиск по тексту/заголовку/тегам
    if search:
        # ищем по полю title, text — и по совпадению с тегом если в запросе # или просто слово
        like = f"%{search}%"
        tag_q = search.lstrip("#").lower()
        q = q.outerjoin(Post.tags).filter(
            db.or_(
                Post.title.ilike(like),
                Post.text.ilike(like),
                Tag.name == tag_q,
            )
        ).distinct()

    # лента подписок — только посты тех на кого подписан
    posts = []
    if feed == "following" and current_user.is_authenticated:
        followed_ids = [f.followed_id for f in current_user.following.all()]
        if followed_ids:
            q = q.filter(Post.author_id.in_(followed_ids))
        else:
            q = None

    if q is not None:
        if sort == "top":
            # сортировка по лайкам после to_dict — лень делать subquery, постов будет немного
            posts = [p.to_dict_for_template(current_user) for p in q.all()]
            posts.sort(key=lambda p: p["likes"], reverse=True)
        else:
            posts_query = q.order_by(Post.post_date.desc()).all()
            posts = [p.to_dict_for_template(current_user) for p in posts_query]

    ctx = dict(posts=posts, sort=sort, feed=feed, cat_id=cat_id, tag=tag, search=search)
    # для AJAX — только список карточек, без обвязки
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return render_template("_feed_posts.html", **ctx)

    return render_template("index.html", **ctx)


@app.route("/post/<int:post_id>")
def show_post(post_id):
    post_obj = Post.query.get_or_404(post_id)

    # свои же просмотры не считаем — иначе автор сам себе накручивает каждым обновлением
    if not current_user.is_authenticated or current_user.id != post_obj.author_id:
        post_obj.views = (post_obj.views or 0) + 1
        db.session.commit()

    post_dict = post_obj.to_dict_for_template(current_user)
    # отдаём только верхний уровень, шаблон сам рекурсивно проходит по replies
    root_comments = post_obj.root_comments()
    comments_count = post_obj.comments_count()

    return render_template(
        "post.html",
        post=post_dict,
        root_comments=root_comments,
        comments_count=comments_count,
    )


@app.route("/post/new", methods=["GET", "POST"])
@login_required
def new_post():
    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        text = (request.form.get("text") or "").strip()
        cat_id = request.form.get("category_id", type=int)

        if not title or not text:
            flash("Заголовок и текст обязательны", "danger")
            return redirect(url_for("new_post"))

        if len(title) > 200:
            flash("Заголовок слишком длинный", "danger")
            return redirect(url_for("new_post"))

        # категория обязательна — если не выбрана или невалидна, ставим "Разное"
        category = Category.query.get(cat_id) if cat_id else None
        if not category:
            category = Category.query.filter_by(name="Разное").first()
        if not category:
            flash("Нет ни одной категории — попроси админа добавить", "danger")
            return redirect(url_for("new_post"))

        image_name = None
        file = request.files.get("image")
        if file and file.filename:
            if not allowed_file(file.filename):
                flash("Неподдерживаемый формат картинки", "danger")
                return redirect(url_for("new_post"))
            image_name = save_image(file, POST_IMG_DIR)

        post = Post(
            title=title,
            text=text,
            category_id=category.id,
            author_id=current_user.id,
            image=image_name,
        )
        db.session.add(post)
        db.session.flush()  # чтоб у поста появился id перед привязкой тегов

        # теги
        tag_names = parse_tags(request.form.get("tags"))
        attach_tags_to_post(post, tag_names)

        db.session.commit()

        flash("Пост опубликован", "success")
        return redirect(url_for("show_post", post_id=post.id))

    return render_template("new_post.html")


@app.route("/post/<int:post_id>/edit", methods=["GET", "POST"])
@login_required
def edit_post(post_id):
    post = Post.query.get_or_404(post_id)
    if post.author_id != current_user.id:
        abort(403)

    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        text = (request.form.get("text") or "").strip()
        cat_id = request.form.get("category_id", type=int)

        if not title or not text:
            flash("Заголовок и текст обязательны", "danger")
            return redirect(url_for("edit_post", post_id=post.id))

        # категория обязательна — если не выбрана, оставляем текущую
        if cat_id:
            new_cat = Category.query.get(cat_id)
            if new_cat:
                post.category_id = new_cat.id

        post.title = title
        post.text = text
        post.edited_at = datetime.utcnow()

        # галочка «удалить картинку»
        if request.form.get("remove_image"):
            delete_image(post.image, POST_IMG_DIR)
            post.image = None

        # либо если загрузили новую — заменяем (старую с диска тоже сносим)
        file = request.files.get("image")
        if file and file.filename:
            if not allowed_file(file.filename):
                flash("Неподдерживаемый формат картинки", "danger")
                return redirect(url_for("edit_post", post_id=post.id))
            delete_image(post.image, POST_IMG_DIR)
            post.image = save_image(file, POST_IMG_DIR)

        # теги — полностью перепривязываем
        tag_names = parse_tags(request.form.get("tags"))
        attach_tags_to_post(post, tag_names)

        db.session.commit()
        flash("Пост обновлён", "success")
        return redirect(url_for("show_post", post_id=post.id))

    return render_template("edit_post.html", post=post)


@app.route("/post/<int:post_id>/delete", methods=["POST"])
@login_required
def delete_post(post_id):
    post = Post.query.get_or_404(post_id)
    if post.author_id != current_user.id:
        abort(403)
    delete_image(post.image, POST_IMG_DIR)
    db.session.delete(post)
    db.session.commit()
    flash("Пост удалён", "info")
    return redirect(url_for("main_page"))


@app.route("/post/<int:post_id>/like", methods=["POST"])
@login_required
def like_post(post_id):
    post = Post.query.get_or_404(post_id)
    existing = Like.query.filter_by(user_id=current_user.id, post_id=post.id).first()
    # уже лайкал — снимаем, иначе ставим. Тогл по сути
    if existing:
        db.session.delete(existing)
        liked = False
    else:
        db.session.add(Like(user_id=current_user.id, post_id=post.id))
        liked = True
    db.session.commit()

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"liked": liked, "count": post.likes_count()})

    return redirect(request.referrer or url_for("show_post", post_id=post.id))


@app.route("/post/<int:post_id>/comments")
def post_comments(post_id):
    # для подгрузки комментов прямо в ленте, без захода в пост
    post = Post.query.get_or_404(post_id)
    comments = post.comments.all()
    return jsonify({
        "comments": [{
            "id": c.id,
            "text": c.text,
            "author": c.author.nickname,
            "author_id": c.author_id,
            "time": c.created_at.strftime("%d.%m.%Y %H:%M"),
            "is_mine": current_user.is_authenticated and current_user.id == c.author_id,
        } for c in comments],
    })


@app.route("/post/<int:post_id>/comment", methods=["POST"])
@login_required
def add_comment(post_id):
    post = Post.query.get_or_404(post_id)
    text = (request.form.get("text") or "").strip()
    parent_id = request.form.get("parent_id", type=int)
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    if not text:
        if is_ajax:
            return jsonify({"error": "пусто"}), 400
        flash("Комментарий пустой", "danger")
        return redirect(url_for("show_post", post_id=post.id))

    if len(text) > 1000:
        if is_ajax:
            return jsonify({"error": "слишком длинный"}), 400
        flash("Слишком длинный комментарий", "danger")
        return redirect(url_for("show_post", post_id=post.id))

    # проверка на parent — должен быть валидный коммент того же поста
    parent = None
    if parent_id:
        parent = Comment.query.filter_by(id=parent_id, post_id=post.id).first()
        if not parent:
            if is_ajax:
                return jsonify({"error": "родитель не найден"}), 400
            flash("Не нашёл коммент на который отвечаешь", "danger")
            return redirect(url_for("show_post", post_id=post.id))

        # ограничиваем глубину: считаем сколько уровней от parent до корня.
        # depth=0 для корня, добавляем дочерний — будет depth=1, и т.д. Максимум 2
        # (значит можно поставить ответ на ответ, но не глубже)
        depth = 0
        cur = parent
        while cur.parent_id:
            depth += 1
            cur = cur.parent
        if depth >= 2:
            # схлопываем — крепим к тому же родителю что и parent (поднимаемся на уровень)
            parent_id = parent.parent_id or parent.id

    c = Comment(
        text=text,
        author_id=current_user.id,
        post_id=post.id,
        parent_id=parent_id,
    )
    db.session.add(c)
    db.session.commit()

    if is_ajax:
        return jsonify({
            "id": c.id,
            "text": c.text,
            "author": c.author.nickname,
            "author_id": c.author_id,
            "time": c.created_at.strftime("%d.%m.%Y %H:%M"),
            "parent_id": c.parent_id,
            "comments_count": post.comments_count(),
        })

    return redirect(url_for("show_post", post_id=post.id))


@app.route("/comment/<int:comment_id>/delete", methods=["POST"])
@login_required
def delete_comment(comment_id):
    c = Comment.query.get_or_404(comment_id)
    if c.author_id != current_user.id:
        abort(403)
    post_id = c.post_id
    db.session.delete(c)
    db.session.commit()
    return redirect(url_for("show_post", post_id=post_id))


@app.route("/comment/<int:comment_id>/react", methods=["POST"])
@login_required
def react_to_comment(comment_id):
    c = Comment.query.get_or_404(comment_id)
    emoji = request.form.get("emoji", "")
    # пускаем только то что в нашем списке, а то юзеры понапихают всякого
    if emoji not in ALLOWED_REACTIONS:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"error": "bad emoji"}), 400
        abort(400)

    existing = Reaction.query.filter_by(
        user_id=current_user.id, comment_id=c.id, emoji=emoji
    ).first()
    if existing:
        # повторный клик по той же реакции — снимаем
        db.session.delete(existing)
    else:
        # лимит: одновременно у юзера может быть только MAX_REACTIONS_PER_USER реакций
        # на коммент. Если упёрлись — удаляем самую старую
        user_reactions = Reaction.query.filter_by(
            user_id=current_user.id, comment_id=c.id
        ).order_by(Reaction.created_at.asc()).all()
        while len(user_reactions) >= MAX_REACTIONS_PER_USER:
            db.session.delete(user_reactions.pop(0))
        db.session.add(Reaction(user_id=current_user.id, comment_id=c.id, emoji=emoji))
    db.session.commit()

    # AJAX — отдаём JSON со свежей сводкой, страница перерисует сама
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({
            "ok": True,
            "reactions": c.reactions_summary(current_user),
        })

    # если JS отвалился — старый редирект работает
    return redirect(url_for("show_post", post_id=c.post_id) + f"#comment-{c.id}")


@app.route("/follow/<int:user_id>", methods=["POST"])
@login_required
def follow_user(user_id):
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash("На себя подписаться нельзя", "warning")
        return redirect(url_for("profile", user_id=user.id))
    current_user.follow(user)
    db.session.commit()
    return redirect(request.referrer or url_for("profile", user_id=user.id))


@app.route("/unfollow/<int:user_id>", methods=["POST"])
@login_required
def unfollow_user(user_id):
    user = User.query.get_or_404(user_id)
    current_user.unfollow(user)
    db.session.commit()
    return redirect(request.referrer or url_for("profile", user_id=user.id))


@app.route("/profile")
@app.route("/profile/<int:user_id>")
def profile(user_id=None):
    # без id — показываем свой профиль (если залогинен)
    if user_id is None:
        if not current_user.is_authenticated:
            return redirect(url_for("login"))
        user = current_user
    else:
        user = User.query.get_or_404(user_id)

    user_posts = (
        Post.query.filter_by(author_id=user.id)
        .order_by(Post.post_date.desc())
        .all()
    )
    posts = [p.to_dict_for_template(current_user) for p in user_posts]

    is_following = False
    if current_user.is_authenticated and current_user.id != user.id:
        is_following = current_user.is_following(user)

    return render_template(
        "profile.html", user=user, posts=posts, is_following=is_following
    )


@app.route("/profile/edit", methods=["GET", "POST"])
@login_required
def edit_profile():
    if request.method == "POST":
        bio = (request.form.get("bio") or "").strip()
        if len(bio) > 300:
            flash("Био слишком длинное (макс 300)", "danger")
            return redirect(url_for("edit_profile"))
        current_user.bio = bio

        file = request.files.get("avatar")
        if file and file.filename:
            if not allowed_file(file.filename):
                flash("Неподдерживаемый формат аватарки", "danger")
                return redirect(url_for("edit_profile"))
            # старую сносим, иначе намусорится
            delete_image(current_user.avatar, AVATAR_DIR)
            current_user.avatar = save_image(file, AVATAR_DIR, max_side=400)

        if request.form.get("remove_avatar"):
            delete_image(current_user.avatar, AVATAR_DIR)
            current_user.avatar = None

        db.session.commit()
        flash("Профиль обновлён", "success")
        return redirect(url_for("profile"))

    return render_template("edit_profile.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main_page"))

    if request.method == "POST":
        login_value = request.form.get("login")
        password = request.form.get("password")

        # пускаем войти и по нику и по почте — оба уникальны
        user = User.query.filter(
            (User.nickname == login_value) | (User.mail == login_value)
        ).first()

        if user and user.check_password(password):
            login_user(user)
            flash("Успешный вход", "success")
            next_page = request.args.get("next")
            return redirect(next_page or url_for("main_page"))
        else:
            flash("Неверный логин или пароль", "danger")
            return redirect(url_for("login"))

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Ты вышел из аккаунта", "info")
    return redirect(url_for("main_page"))


@app.route("/profile/api-token", methods=["POST"])
@login_required
def regenerate_api_token():
    """генерируем или пересоздаём API-токен текущего юзера"""
    from api import generate_token
    current_user.api_token = generate_token()
    db.session.commit()
    flash("Токен API создан. Сохрани его — он показывается один раз.", "success")
    return redirect(url_for("edit_profile"))


@app.route("/profile/api-token/revoke", methods=["POST"])
@login_required
def revoke_api_token():
    """отключает токен — после этого API запросы юзера не пройдут"""
    current_user.api_token = None
    db.session.commit()
    flash("Токен отозван", "info")
    return redirect(url_for("edit_profile"))


@app.route("/api/docs")
def api_docs():
    """страница с описанием API"""
    return render_template("api_docs.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("main_page"))

    if request.method == "POST":
        nickname = (request.form.get("nickname") or "").strip()
        email = (request.form.get("email") or "").strip()
        password = request.form.get("password") or ""

        if not nickname or not email or not password:
            flash("Заполни все поля", "danger")
            return redirect(url_for("register"))

        if len(password) < 6:
            flash("Пароль должен быть минимум 6 символов", "danger")
            return redirect(url_for("register"))

        if User.query.filter((User.nickname == nickname) | (User.mail == email)).first():
            flash("Такой ник или email уже существует", "danger")
            return redirect(url_for("register"))

        user = User(nickname=nickname, mail=email)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        flash("Регистрация успешна, войди в аккаунт", "success")
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/toggle-theme", methods=["POST"])
def toggle_theme():
    current = request.cookies.get("theme", "light")
    new_theme = "dark" if current == "light" else "light"
    resp = make_response(redirect(request.referrer or url_for("main_page")))
    # год хранится — нормально, не пароль же
    resp.set_cookie("theme", new_theme, max_age=60 * 60 * 24 * 365)
    return resp


# ловит когда юзер пытается залить слишком жирный файл — Flask кидает 413
@app.errorhandler(413)
def too_large(e):
    flash("Файл слишком большой (макс 5 МБ)", "danger")
    return redirect(request.referrer or url_for("main_page"))


# === админка ===

ADMIN_EMAIL = "admin@as.local"
ADMIN_NICKNAME = "admin"
# дефолтный пароль — обязательно сменить после первого входа
ADMIN_DEFAULT_PASSWORD = os.environ.get("ADMIN_PASSWORD", "Admin12345")


def ensure_admin_and_defaults():
    """при первом запуске создаём админа и пару базовых категорий"""
    if not User.query.filter_by(mail=ADMIN_EMAIL).first():
        admin = User(nickname=ADMIN_NICKNAME, mail=ADMIN_EMAIL, is_admin=True)
        admin.set_password(ADMIN_DEFAULT_PASSWORD)
        db.session.add(admin)

    # хотя бы одна категория должна быть, иначе нельзя создавать посты
    if not Category.query.first():
        defaults = [
            ("Разное", 100),
            ("Математика", 10),
            ("История", 20),
            ("Программирование", 30),
        ]
        for name, order in defaults:
            db.session.add(Category(name=name, sort_order=order))

    db.session.commit()


@app.route("/admin")
@admin_required
def admin_index():
    return redirect(url_for("admin_categories"))


@app.route("/admin/categories", methods=["GET", "POST"])
@admin_required
def admin_categories():
    if request.method == "POST":
        action = request.form.get("action")

        if action == "add":
            name = (request.form.get("name") or "").strip()
            if not name:
                flash("Имя категории не может быть пустым", "danger")
            elif Category.query.filter_by(name=name).first():
                flash("Категория с таким именем уже есть", "warning")
            elif len(name) > 50:
                flash("Слишком длинное имя (макс 50)", "danger")
            else:
                # порядок — в конец списка
                max_order = db.session.query(db.func.max(Category.sort_order)).scalar() or 0
                db.session.add(Category(name=name, sort_order=max_order + 10))
                db.session.commit()
                flash("Категория добавлена", "success")

        elif action == "rename":
            cat = Category.query.get_or_404(request.form.get("id", type=int))
            new_name = (request.form.get("name") or "").strip()
            if not new_name:
                flash("Имя не может быть пустым", "danger")
            else:
                cat.name = new_name
                db.session.commit()
                flash("Переименовано", "success")

        elif action == "delete":
            cat = Category.query.get_or_404(request.form.get("id", type=int))
            # нельзя удалить "Разное" — туда переезжают посты из удаляемых категорий
            fallback = Category.query.filter_by(name="Разное").first()
            if fallback and cat.id == fallback.id:
                flash("Категорию 'Разное' удалять нельзя — это запасной вариант", "warning")
            else:
                # все посты этой категории переезжают в "Разное"
                if fallback:
                    Post.query.filter_by(category_id=cat.id).update({"category_id": fallback.id})
                db.session.delete(cat)
                db.session.commit()
                flash("Категория удалена, посты перенесены в 'Разное'", "info")

        return redirect(url_for("admin_categories"))

    return render_template("admin_categories.html")


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        ensure_admin_and_defaults()
    app.run(port=8080, host="127.0.0.1", debug=True)

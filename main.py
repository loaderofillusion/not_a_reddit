from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, abort, make_response,
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
import uuid

from models import db, User, Post, Like, Comment, Follow, Reaction, ALLOWED_REACTIONS

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

app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "very-secret-key")
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_PATH}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = MAX_IMAGE_SIZE

db.init_app(app)

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
    return {"theme": theme, "ALLOWED_REACTIONS": ALLOWED_REACTIONS}


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


@app.route("/")
def main_page():
    sort = request.args.get("sort", "new")
    feed = request.args.get("feed", "all")
    q = Post.query

    # лента подписок — только посты тех на кого подписан
    if feed == "following" and current_user.is_authenticated:
        followed_ids = [f.followed_id for f in current_user.following.all()]
        if not followed_ids:
            return render_template("index.html", posts=[], sort=sort, feed=feed)
        q = q.filter(Post.author_id.in_(followed_ids))

    if sort == "top":
        # сортировка по лайкам после to_dict — лень делать subquery, постов будет немного
        posts = [p.to_dict_for_template(current_user) for p in q.all()]
        posts.sort(key=lambda p: p["likes"], reverse=True)
    else:
        posts_query = q.order_by(Post.post_date.desc()).all()
        posts = [p.to_dict_for_template(current_user) for p in posts_query]

    return render_template("index.html", posts=posts, sort=sort, feed=feed)


@app.route("/post/<int:post_id>")
def show_post(post_id):
    post_obj = Post.query.get_or_404(post_id)

    # инкрементим просмотры на каждый заход. На накрутки забиваем — не та задача
    post_obj.views = (post_obj.views or 0) + 1
    db.session.commit()

    post_dict = post_obj.to_dict_for_template(current_user)
    comments = post_obj.comments.all()

    # к каждому комменту прикладываем сводку реакций
    comments_data = [
        {"obj": c, "reactions": c.reactions_summary(current_user)}
        for c in comments
    ]

    return render_template("post.html", post=post_dict, comments=comments_data)


@app.route("/post/new", methods=["GET", "POST"])
@login_required
def new_post():
    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        text = (request.form.get("text") or "").strip()
        category = (request.form.get("category") or "").strip() or None

        if not title or not text:
            flash("Заголовок и текст обязательны", "danger")
            return redirect(url_for("new_post"))

        if len(title) > 200:
            flash("Заголовок слишком длинный", "danger")
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
            category=category,
            author_id=current_user.id,
            image=image_name,
        )
        db.session.add(post)
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
        category = (request.form.get("category") or "").strip() or None

        if not title or not text:
            flash("Заголовок и текст обязательны", "danger")
            return redirect(url_for("edit_post", post_id=post.id))

        post.title = title
        post.text = text
        post.category = category
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
    else:
        db.session.add(Like(user_id=current_user.id, post_id=post.id))
    db.session.commit()
    return redirect(request.referrer or url_for("show_post", post_id=post.id))


@app.route("/post/<int:post_id>/comment", methods=["POST"])
@login_required
def add_comment(post_id):
    post = Post.query.get_or_404(post_id)
    text = (request.form.get("text") or "").strip()

    if not text:
        flash("Комментарий пустой", "danger")
        return redirect(url_for("show_post", post_id=post.id))

    if len(text) > 1000:
        flash("Слишком длинный комментарий", "danger")
        return redirect(url_for("show_post", post_id=post.id))

    db.session.add(Comment(text=text, author_id=current_user.id, post_id=post.id))
    db.session.commit()
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
        abort(400)

    existing = Reaction.query.filter_by(
        user_id=current_user.id, comment_id=c.id, emoji=emoji
    ).first()
    if existing:
        db.session.delete(existing)
    else:
        db.session.add(Reaction(user_id=current_user.id, comment_id=c.id, emoji=emoji))
    db.session.commit()
    # якорь чтоб юзера сразу подкинуло к комменту, а не наверх страницы
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


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(port=8080, host="127.0.0.1", debug=True)
# main.py
from flask import Flask, render_template, request, redirect, url_for, flash, abort
from flask_login import (
    LoginManager,
    login_user,
    logout_user,
    login_required,
    current_user,
)
import os

from models import db, User, Post, Like, Comment


app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "db.db")

app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "very-secret-key")
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_PATH}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Войди, чтобы продолжить"
login_manager.login_message_category = "warning"


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


@app.route("/")
def main_page():
    # сортировка: новые сверху, ?sort=top — по лайкам
    sort = request.args.get("sort", "new")
    q = Post.query
    if sort == "top":
        # тут можно было бы через subquery считать лайки
        # отсортируем уже после to_dict
        posts_query = q.all()
        posts = [p.to_dict_for_template(current_user) for p in posts_query]
        posts.sort(key=lambda p: p["likes"], reverse=True)
    else:
        posts_query = q.order_by(Post.post_date.desc()).all()
        posts = [p.to_dict_for_template(current_user) for p in posts_query]

    return render_template("index.html", posts=posts, sort=sort)


@app.route("/post/<int:post_id>")
def show_post(post_id):
    post_obj = Post.query.get(post_id)
    if post_obj is None:
        abort(404)

    # увеличиваем счётчик просмотров 
    post_obj.views = (post_obj.views or 0) + 1
    db.session.commit()

    post_dict = post_obj.to_dict_for_template(current_user)
    comments = post_obj.comments.all()
    return render_template("post.html", post=post_dict, comments=comments)


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

        post = Post(
            title=title,
            text=text,
            category=category,
            author_id=current_user.id,
        )
        db.session.add(post)
        db.session.commit()

        flash("Пост опубликован", "success")
        return redirect(url_for("show_post", post_id=post.id))

    return render_template("new_post.html")


@app.route("/post/<int:post_id>/like", methods=["POST"])
@login_required
def like_post(post_id):
    post = Post.query.get_or_404(post_id)

    existing = Like.query.filter_by(user_id=current_user.id, post_id=post.id).first()
    if existing:
        # повторный клик — снимаем лайк
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

    c = Comment(text=text, author_id=current_user.id, post_id=post.id)
    db.session.add(c)
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


@app.route("/profile")
@app.route("/profile/<int:user_id>")
def profile(user_id=None):
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

    return render_template("profile.html", user=user, posts=posts)


@app.route("/profile/edit", methods=["GET", "POST"])
@login_required
def edit_profile():
    if request.method == "POST":
        bio = (request.form.get("bio") or "").strip()
        if len(bio) > 300:
            flash("Био слишком длинное (макс 300)", "danger")
            return redirect(url_for("edit_profile"))
        current_user.bio = bio
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


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(port=8080, host="127.0.0.1", debug=True)

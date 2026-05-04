# models.py
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class User(db.Model, UserMixin):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    nickname = db.Column(db.String, unique=True, nullable=False)
    mail = db.Column(db.String, unique=True, nullable=False)
    password_hash = db.Column(db.String, nullable=False)
    bio = db.Column(db.String(300), default="")
    registered_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class Post(db.Model):
    __tablename__ = "posts"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String, nullable=False)
    text = db.Column(db.Text, nullable=False)

    author_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    author = db.relationship("User", backref="posts")

    post_date = db.Column(db.DateTime, default=datetime.utcnow)
    category = db.Column(db.String, nullable=True)
    views = db.Column(db.Integer, default=0)

    likes = db.relationship(
        "Like", backref="post", cascade="all, delete-orphan", lazy="dynamic"
    )
    comments = db.relationship(
        "Comment", backref="post", cascade="all, delete-orphan",
        lazy="dynamic", order_by="Comment.created_at.desc()"
    )

    def likes_count(self):
        return self.likes.count()

    def comments_count(self):
        return self.comments.count()

    def liked_by(self, user):
        # для анонимов сразу False
        if not user or not user.is_authenticated:
            return False
        return self.likes.filter_by(user_id=user.id).first() is not None

    def to_dict_for_template(self, current_user=None):
        return {
            "post_id": self.id,
            "title": self.title,
            "text": self.text,
            "author": self.author.nickname if self.author else "Неизвестный",
            "author_id": self.author_id,
            "time": self.post_date.strftime("%d.%m.%Y %H:%M") if self.post_date else "",
            "likes": self.likes_count(),
            "comments": self.comments_count(),
            "category": self.category,
            "views": self.views,
            "liked": self.liked_by(current_user),
        }


class Like(db.Model):
    __tablename__ = "likes"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    post_id = db.Column(db.Integer, db.ForeignKey("posts.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # один юзер не может лайкнуть один пост дважды
    __table_args__ = (db.UniqueConstraint("user_id", "post_id", name="uniq_like"),)


class Comment(db.Model):
    __tablename__ = "comments"

    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    author_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    author = db.relationship("User", backref="comments")

    post_id = db.Column(db.Integer, db.ForeignKey("posts.id"), nullable=False)

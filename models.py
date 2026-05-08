from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

# db создаётся тут без app, привязываем уже в main через init_app
db = SQLAlchemy()


class User(db.Model, UserMixin):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    nickname = db.Column(db.String, unique=True, nullable=False)
    mail = db.Column(db.String, unique=True, nullable=False)
    password_hash = db.Column(db.String, nullable=False)
    bio = db.Column(db.String(300), default="")
    avatar = db.Column(db.String, nullable=True)  # имя файла в static/uploads/avatars
    registered_at = db.Column(db.DateTime, default=datetime.utcnow)

    # подписки на других — это записи где этот юзер follower
    following = db.relationship(
        "Follow",
        foreign_keys="Follow.follower_id",
        backref="follower",
        cascade="all, delete-orphan",
        lazy="dynamic",
    )
    # подписчики этого юзера — записи где он followed
    followers = db.relationship(
        "Follow",
        foreign_keys="Follow.followed_id",
        backref="followed",
        cascade="all, delete-orphan",
        lazy="dynamic",
    )

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def is_following(self, other):
        if not other:
            return False
        return self.following.filter_by(followed_id=other.id).first() is not None

    def follow(self, other):
        # на себя подписываться нельзя
        if other.id == self.id:
            return
        # и второй раз тоже — UniqueConstraint конечно не пустит, но лучше проверим заранее
        if not self.is_following(other):
            db.session.add(Follow(follower_id=self.id, followed_id=other.id))

    def unfollow(self, other):
        f = self.following.filter_by(followed_id=other.id).first()
        if f:
            db.session.delete(f)

    def followers_count(self):
        return self.followers.count()

    def following_count(self):
        return self.following.count()


class Follow(db.Model):
    __tablename__ = "follows"
    id = db.Column(db.Integer, primary_key=True)
    follower_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    followed_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    # одна и та же подписка только один раз
    __table_args__ = (
        db.UniqueConstraint("follower_id", "followed_id", name="uniq_follow"),
    )


class Post(db.Model):
    __tablename__ = "posts"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String, nullable=False)
    text = db.Column(db.Text, nullable=False)
    image = db.Column(db.String, nullable=True)  # имя файла в static/uploads/posts

    author_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    author = db.relationship("User", backref="posts")

    post_date = db.Column(db.DateTime, default=datetime.utcnow)
    edited_at = db.Column(db.DateTime, nullable=True)  # None = ни разу не редактировался
    category = db.Column(db.String, nullable=True)
    views = db.Column(db.Integer, default=0)

    # cascade чтобы при удалении поста ушли и его лайки/комменты
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
        # для анонимов сразу False, чтоб не делать лишний запрос
        if not user or not user.is_authenticated:
            return False
        return self.likes.filter_by(user_id=user.id).first() is not None

    def to_dict_for_template(self, current_user=None):
        return {
            "post_id": self.id,
            "title": self.title,
            "text": self.text,
            "image": self.image,
            "author": self.author.nickname if self.author else "Неизвестный",
            "author_id": self.author_id,
            "author_avatar": self.author.avatar if self.author else None,
            "time": self.post_date.strftime("%d.%m.%Y %H:%M") if self.post_date else "",
            "edited": self.edited_at is not None,
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

    reactions = db.relationship(
        "Reaction", backref="comment", cascade="all, delete-orphan", lazy="dynamic"
    )

    def reactions_summary(self, current_user=None):
        # собираем сводку: какие эмодзи стоят, сколько раз и моё ли
        # формат: [{"emoji": "👍", "count": 3, "mine": True}, ...]
        result = []
        for emoji in ALLOWED_REACTIONS:
            qs = self.reactions.filter_by(emoji=emoji)
            count = qs.count()
            if count == 0:
                continue
            mine = False
            if current_user and current_user.is_authenticated:
                mine = qs.filter_by(user_id=current_user.id).first() is not None
            result.append({"emoji": emoji, "count": count, "mine": mine})
        return result


# Жёсткий список разрешённых эмодзи. Если хочется добавить — просто допиши сюда.
# Без этого юзеры могли бы пихать любую эмодзяшку, и в БД был бы зоопарк.
ALLOWED_REACTIONS = ["👍", "❤️", "🤔", "🔥", "😂"]


class Reaction(db.Model):
    __tablename__ = "reactions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    comment_id = db.Column(db.Integer, db.ForeignKey("comments.id"), nullable=False)
    emoji = db.Column(db.String(8), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # один юзер = одна реакция данного типа на коммент.
    # Разные эмодзи на один коммент ставить можно — потому emoji в ключе тоже
    __table_args__ = (
        db.UniqueConstraint("user_id", "comment_id", "emoji", name="uniq_reaction"),
    )
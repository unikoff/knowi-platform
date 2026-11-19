"""SQL registration implementation."""

from pymentor_core.adapters.sql import models
from pymentor_core.modules.identity.registration import EmailAlreadyExists
from sqlalchemy import text
from uuid import uuid4


class Registration:
    def register(self, user, db):
        existing_user = (
            db.query(models.User).filter(models.User.email == user.email).first()
        )
        if existing_user:
            raise EmailAlreadyExists()
        new_user = models.User(
            username=user.username, email=user.email, password=user.password
        )
        db.add(new_user)
        db.flush()
        db.execute(text('INSERT INTO account_lifecycles(user_id,lifecycle_id) VALUES (:uid,:ref)'),
                   {'uid': new_user.id, 'ref': str(uuid4())})
        db.commit()
        db.refresh(new_user)
        return new_user

from typing import Literal

from pydantic import BaseModel

from app.auth import Role


class IdentityRead(BaseModel):
    subject: str
    role: Role
    auth_mode: Literal["local_demo"] = "local_demo"

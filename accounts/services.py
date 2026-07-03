import logging
from typing import Any, Optional

from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import AbstractBaseUser

logger = logging.getLogger(__name__)


class UserService:

    @staticmethod
    def authenticate_and_login(
        request: Any, username: str, password: str
    ) -> Optional[AbstractBaseUser]:
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            logger.info("Usuario %s fez login com sucesso.", user.username)
            return user
        logger.warning("Tentativa de login malsucedida para o usuario: %s", username)
        return None

    @staticmethod
    def register_and_login(request: Any, form: Any) -> AbstractBaseUser:
        user = form.save()
        login(request, user, backend="accounts.backends.EmailOrUsernameModelBackend")
        logger.info("Novo usuario registrado: %s", user.username)
        return user

    @staticmethod
    def logout_user(request: Any) -> str:
        username = (
            request.user.username if request.user.is_authenticated else "anonymous"
        )
        logout(request)
        logger.info("Usuario %s fez logout.", username)
        return username

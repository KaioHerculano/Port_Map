import logging
from typing import Any, Union

from django import forms
from django.contrib import messages
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import redirect
from django.views.generic import FormView, View

from .services import UserService

logger = logging.getLogger(__name__)


class UserLoginForm(forms.Form):
    username = forms.CharField(
        label="Usuário ou E-mail",
        widget=forms.TextInput(
            attrs={
                "class": "form-input",
                "placeholder": "Digite seu usuário ou e-mail",
                "autocomplete": "username",
            }
        ),
    )
    password = forms.CharField(
        label="Senha",
        widget=forms.PasswordInput(
            attrs={
                "class": "form-input",
                "placeholder": "Digite sua senha",
                "autocomplete": "current-password",
            }
        ),
    )


class UserLoginView(FormView):
    template_name = "login.html"
    form_class = UserLoginForm
    success_url = "/"

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if request.user.is_authenticated:
            return redirect("dashboard")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(
        self, form: UserLoginForm
    ) -> Union[HttpResponse, HttpResponseRedirect]:
        username = form.cleaned_data.get("username")
        password = form.cleaned_data.get("password")

        user = UserService.authenticate_and_login(self.request, username, password)
        if user is not None:
            messages.success(self.request, f"Bem-vindo de volta, {user.username}!")
            return redirect(self.get_success_url())
        else:
            messages.error(
                self.request,
                "Credenciais inválidas. Verifique o usuário/e-mail e a senha.",
            )
            return self.form_invalid(form)


class UserLogoutView(View):
    def post(
        self, request: HttpRequest, *args: Any, **kwargs: Any
    ) -> HttpResponseRedirect:
        UserService.logout_user(request)
        messages.info(request, "Você foi desconectado com sucesso.")
        return redirect("login")


def custom_csrf_failure(request: HttpRequest, reason: str = "") -> HttpResponse:
    """
    Trata falhas de CSRF. Se o usuário já estiver autenticado e tentar logar novamente
    com um token CSRF antigo, redireciona para a dashboard.
    """
    from django.views.csrf import csrf_failure

    if request.user.is_authenticated:
        return redirect("dashboard")
    return csrf_failure(request, reason=reason)

import logging
from django.shortcuts import render, redirect
from django.contrib.auth import login, logout, authenticate
from django.contrib import messages
from django import forms
from django.contrib.auth import get_user_model
from django.views.generic import FormView, CreateView, View
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from typing import Any, Dict, Union

logger = logging.getLogger(__name__)

# Form Definitions
class UserLoginForm(forms.Form):
    username = forms.CharField(
        label="Usuário ou E-mail", 
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': 'Digite seu usuário ou e-mail',
            'autocomplete': 'username'
        })
    )
    password = forms.CharField(
        label="Senha", 
        widget=forms.PasswordInput(attrs={
            'class': 'form-input',
            'placeholder': 'Digite sua senha',
            'autocomplete': 'current-password'
        })
    )


class UserSignupForm(forms.ModelForm):
    username = forms.CharField(
        label="Usuário", 
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': 'Escolha um nome de usuário'
        })
    )
    email = forms.EmailField(
        label="E-mail", 
        widget=forms.EmailInput(attrs={
            'class': 'form-input',
            'placeholder': 'exemplo@email.com'
        })
    )
    password = forms.CharField(
        label="Senha", 
        widget=forms.PasswordInput(attrs={
            'class': 'form-input',
            'placeholder': 'Crie uma senha'
        })
    )

    class Meta:
        model = get_user_model()
        fields = ['username', 'email', 'password']

    def clean_email(self) -> str:
        email = self.cleaned_data.get('email', '').lower()
        UserModel = get_user_model()
        if UserModel.objects.filter(email=email).exists():
            raise forms.ValidationError("Este endereço de e-mail já está em uso.")
        return email

    def clean_username(self) -> str:
        username = self.cleaned_data.get('username', '')
        UserModel = get_user_model()
        if UserModel.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("Este nome de usuário já está em uso.")
        return username

    def save(self, commit: bool = True) -> Any:
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password"])
        if commit:
            user.save()
        return user


# Class-Based View (CBV) Definitions
class UserLoginView(FormView):
    template_name = 'accounts/login.html'
    form_class = UserLoginForm
    success_url = '/'

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if request.user.is_authenticated:
            return redirect('dashboard')
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form: UserLoginForm) -> Union[HttpResponse, HttpResponseRedirect]:
        username = form.cleaned_data.get('username')
        password = form.cleaned_data.get('password')
        
        user = authenticate(self.request, username=username, password=password)
        
        if user is not None:
            login(self.request, user)
            messages.success(self.request, f"Bem-vindo de volta, {user.username}!")
            logger.info("Usuario %s fez login com sucesso.", user.username)
            return redirect(self.get_success_url())
        else:
            messages.error(self.request, "Credenciais inválidas. Verifique o usuário/e-mail e a senha.")
            logger.warning("Tentativa de login malsucedida para o usuario: %s", username)
            return self.form_invalid(form)


class UserSignupView(CreateView):
    model = get_user_model()
    form_class = UserSignupForm
    template_name = 'accounts/signup.html'
    success_url = '/'

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if request.user.is_authenticated:
            return redirect('dashboard')
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form: UserSignupForm) -> HttpResponseRedirect:
        user = form.save()
        login(self.request, user, backend='accounts.backends.EmailOrUsernameModelBackend')
        messages.success(self.request, f"Conta criada com sucesso! Bem-vindo, {user.username}!")
        logger.info("Novo usuario registrado: %s", user.username)
        return redirect(self.get_success_url())


class UserLogoutView(View):
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseRedirect:
        username = request.user.username if request.user.is_authenticated else "anonimo"
        logout(request)
        messages.info(request, "Você foi desconectado com sucesso.")
        logger.info("Usuario %s fez logout.", username)
        return redirect('login')

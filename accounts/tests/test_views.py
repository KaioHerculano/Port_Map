from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class AccountsViewTests(TestCase):
    def test_login_view_get(self):
        url = reverse("login")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/login.html")

    def test_login_view_post_redirects(self):
        User = get_user_model()
        User.objects.create_user(
            username="existinguser",
            email="existinguser@example.com",
            password="password123",
        )

        url = reverse("login")
        data = {
            "username": "existinguser",
            "password": "password123",
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/")

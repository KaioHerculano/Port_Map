from django.db import models


class Group(models.Model):
    company = models.ForeignKey(
        "accounts.Company", on_delete=models.CASCADE, related_name="groups"
    )
    name = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        app_label = "monitor"

    def __str__(self):
        return self.name

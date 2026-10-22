from django.urls import path

from .documents import DocumentView

urlpatterns = [path("<str:kind>/<uuid:pk>", DocumentView.as_view())]

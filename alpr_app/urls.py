from django.urls import path
from . import views

urlpatterns = [
    # A raiz do site vai abrir a tela do porteiro
    path('', views.index, name='index'),
    # Essa rota é consumida pela tag <img> lá no seu HTML
    path('camera/live/', views.camera_live, name='camera_live'),
]
from django.contrib import admin
from django.urls import path, include # <-- Adicione o include aqui

urlpatterns = [
    path('admin/', admin.site.urls),
    # Diz pro Django que a raiz do site deve procurar as rotas do nosso alpr_app
    path('', include('alpr_app.urls')),
]
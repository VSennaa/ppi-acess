from django.contrib import admin
from .models import Configuracao, EventoLeitura

admin.site.register(Configuracao)
admin.site.register(EventoLeitura)
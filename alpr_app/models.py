from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

class Configuracao(models.Model):
    nome = models.CharField(max_length=100, default="Padrão")
    #rtsp_url = models.URLField(max_length=500)
    rtsp_url = models.CharField(max_length=500, help_text="Coloque a URL RTSP ou digite 0 para USB local.")
    tolerancia_match_percentual = models.PositiveSmallIntegerField(
        default=85,
        validators=[MinValueValidator(80), MaxValueValidator(90)],
        help_text="Percentual mínimo para considerar match de placa (80-90%).",
    )
    intervalo_frames_ms = models.PositiveIntegerField(
        default=250,
        help_text="Intervalo de captura entre frames processados.",
    )
    tentativas_por_evento = models.PositiveSmallIntegerField(
        default=6,
        help_text="Quantidade de tentativas de OCR antes do veredito final.",
    )
    ativo = models.BooleanField(default=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Configuração"
        verbose_name_plural = "Configurações"

    def __str__(self) -> str:
        return self.nome


class EventoLeitura(models.Model):
    class Status(models.TextChoices):
        AUTORIZADO = "autorizado", "Autorizado"
        DESCONHECIDO = "desconhecido", "Desconhecido"

    placa_lida = models.CharField(max_length=20, db_index=True)
    placa_normalizada = models.CharField(max_length=10, db_index=True)
    confianca_ocr = models.FloatField(default=0.0)
    score_match_percentual = models.FloatField(default=0.0)
    status = models.CharField(max_length=20, choices=Status.choices)
    nome_servidor = models.CharField(max_length=150, blank=True, null=True) # <-- Modificado para texto simples
    criado_em = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-criado_em"]
        verbose_name = "Evento de leitura"
        verbose_name_plural = "Eventos de leitura"

    def __str__(self) -> str:
        return f"{self.placa_lida} - {self.get_status_display()}"
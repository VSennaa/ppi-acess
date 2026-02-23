from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Servidor(models.Model):
    """
    Substitui o CSV de servidores autorizados.
    Armazena múltiplas variações de placa para lidar com OCR imperfeito.
    """

    nome = models.CharField(max_length=150)
    matricula = models.CharField(max_length=30, blank=True)
    ativo = models.BooleanField(default=True)
    observacoes = models.TextField(blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["nome"]
        verbose_name = "Servidor"
        verbose_name_plural = "Servidores"

    def __str__(self) -> str:
        return self.nome


class PlacaAutorizada(models.Model):
    """
    Uma pessoa pode ter mais de uma placa autorizada.
    """

    servidor = models.ForeignKey(
        Servidor,
        related_name="placas",
        on_delete=models.CASCADE,
    )
    valor = models.CharField(max_length=10, db_index=True)
    principal = models.BooleanField(default=True)

    class Meta:
        unique_together = ("servidor", "valor")
        verbose_name = "Placa autorizada"
        verbose_name_plural = "Placas autorizadas"

    def __str__(self) -> str:
        return f"{self.valor} ({self.servidor.nome})"


class Configuracao(models.Model):
    """
    Substitui config.json.
    Use singleton (apenas um registro ativo).
    """

    nome = models.CharField(max_length=100, default="Padrão")
    rtsp_url = models.URLField(max_length=500)
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
    servidor = models.ForeignKey(
        Servidor,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="eventos",
    )
    criado_em = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-criado_em"]
        verbose_name = "Evento de leitura"
        verbose_name_plural = "Eventos de leitura"

    def __str__(self) -> str:
        return f"{self.placa_lida} - {self.get_status_display()}"

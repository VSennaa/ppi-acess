# Nome do arquivo de sa°da
$outputFile = "contexto_gemini.txt"

# Cabeáalho
"--- ESTRUTURA DE ARQUIVOS ---" | Out-File -FilePath $outputFile
Get-ChildItem -Recurse | Where-Object { $_.FullName -notmatch "venv|__pycache__|\.git" } | Select-Object FullName | Out-File -FilePath $outputFile -Append

" `n--- CONTEÈDO DOS ARQUIVOS CHAVE ---" | Out-File -FilePath $outputFile -Append

# Lista de arquivos importantes para o Preview
$arquivos = @(
    "alpr_app/views.py",
    "alpr_app/consumers.py",
    "alpr_app/routing.py",
    "core/asgi.py",
    "core/urls.py",
    "main.py"
)

foreach ($arq in $arquivos) {
    if (Test-Path $arq) {
        " `n[ARQUIVO: $arq]" | Out-File -FilePath $outputFile -Append
        Get-Content $arq | Out-File -FilePath $outputFile -Append
    }
}

Write-Host "Arquivo 'contexto_gemini.txt' gerado com sucesso! Copie o conte£do dele e me mande." -ForegroundColor Cyan

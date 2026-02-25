from django.shortcuts import render
from django.http import StreamingHttpResponse
import cv2
import asyncio

# Importamos o módulo inteiro do worker
from alpr_app.services import recognition_worker

def index(request):
    """Renderiza a nossa página HTML do Front-End."""
    return render(request, 'index.html')

# 👇 Transformamos em uma função 'async def'
async def gerador_frames():
    """Gera o feed de vídeo usando async para não travar o servidor Daphne."""
    while True:
        frame_atual = recognition_worker.last_frame
        
        if frame_atual is not None:
            # Converte o frame atual (matriz OpenCV) para JPEG
            ret, jpeg = cv2.imencode('.jpg', frame_atual)
            if ret:
                frame_bytes = jpeg.tobytes()
                
                # Envia no formato MJPEG
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
        else:
            # Substituímos o time.sleep() pelo asyncio.sleep()
            await asyncio.sleep(0.5)
            continue
            
        # Controla a taxa de atualização do preview (aprox 30 FPS) liberando o processador
        await asyncio.sleep(0.03)

# 👇 Transformamos a rota em 'async def'
async def camera_live(request):
    """Rota que entrega o stream de vídeo assíncrono."""
    return StreamingHttpResponse(gerador_frames(), content_type='multipart/x-mixed-replace; boundary=frame')
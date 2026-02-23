from django.shortcuts import render
from django.http import StreamingHttpResponse
import cv2
import numpy as np
import time

def index(request):
    """Renderiza a nossa página HTML do Front-End."""
    return render(request, 'index.html')

def simulador_camera():
    """Gera um feed de vídeo falso para testarmos o Front-End sem travar a câmera real."""
    while True:
        # Cria um frame preto de 640x480
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        cv2.putText(frame, "VIDEO DA CAMERA AQUI", (120, 240), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
        
        # Converte para JPEG
        ret, jpeg = cv2.imencode('.jpg', frame)
        frame_bytes = jpeg.tobytes()
        
        # Envia no formato que o navegador entende como "vídeo ao vivo" (MJPEG)
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
        time.sleep(0.1)

def camera_live(request):
    """Rota que entrega o stream de vídeo."""
    return StreamingHttpResponse(simulador_camera(), content_type='multipart/x-mixed-replace; boundary=frame')
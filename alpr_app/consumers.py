import json

from channels.generic.websocket import AsyncWebsocketConsumer


class PlacaStatusConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.group_name = "placas_status"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def placa_status(self, event):
        await self.send(text_data=json.dumps(event["payload"]))

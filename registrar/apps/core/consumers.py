"""
Defines the Kombu consumer for the registrar project.
"""
from __future__ import absolute_import

from kombu import Exchange, Queue
from kombu.mixins import ConsumerMixin

from registrar.apps.core.models import Program


class ProgramConsumer(ConsumerMixin):

    exchange = Exchange('catalog', type='direct')
    queues = [
        Queue('program_create_queue', exchange, routing_key='catalog.program.create'),
        Queue('program_update_queue', exchange, routing_key='catalog.program.update'),
        Queue('program_delete_queue', exchange, routing_key='catalog.program.delete'),
    ]

    def __init__(self, connection):
        self.connection = connection

    def get_consumers(self, Consumer, channel):
        return [
            Consumer(self.queues, callbacks=[self.on_message], accept=['json']),
        ]

    def on_message(self, body, message):
        payload = message.payload
        if message.delivery_info['routing_key'] == 'catalog.program.create':
            print('CREATED!')
            print(payload)
        elif message.delivery_info['routing_key'] == 'catalog.program.update':
            print('UPDATED!')
            print(payload)
        elif message.delivery_info['routing_key'] == 'catalog.program.delete':
            print('DELETED!')
            print(payload)
        message.ack()

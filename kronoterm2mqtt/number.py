"""A Home Assistant number component, because ha-services does not have one.

The heat pump has settings, not just readings: the sanitary water temperature, the
temperature of each heating loop, the ECO and comfort offsets. The manufacturer's Modbus
documentation marks those registers RW, and without a number component the only way to
change them is on the heat pump's own display.

ha-services ships sensor, binary_sensor, switch and select. This fills the gap in the
same shape, so it can move upstream unchanged if that project wants it.

https://www.home-assistant.io/integrations/number.mqtt/
"""

from collections.abc import Callable
import logging

from ha_services.exceptions import InvalidStateValue
from ha_services.mqtt4homeassistant.components import BaseComponent
from ha_services.mqtt4homeassistant.data_classes import NO_STATE, ComponentConfig, ComponentState
from ha_services.mqtt4homeassistant.device import MqttDevice
from paho.mqtt.client import MQTT_ERR_SUCCESS, Client, MQTTMessageInfo


logger = logging.getLogger(__name__)


def default_number_callback(*, client: Client, component: 'Number', old_state, new_state) -> None:
    logger.info(f'{component.name} changed: {old_state!r} -> {new_state!r}')
    component.set_state(new_state)
    component.publish_state(client)


class Number(BaseComponent):
    def __init__(
        self,
        *,
        device: MqttDevice,
        name: str,
        uid: str,
        min_value: float,
        max_value: float,
        step: float = 0.5,
        unit_of_measurement: str | None = None,
        device_class: str | None = None,
        mode: str = 'box',
        suggested_display_precision: int | None = None,
        callback: Callable = default_number_callback,
        component: str = 'number',
        initial_state=NO_STATE,
    ):
        super().__init__(device=device, name=name, uid=uid, component=component, initial_state=initial_state)

        assert min_value < max_value, f'{min_value=} is not below {max_value=}'
        self.min_value = min_value
        self.max_value = max_value
        self.step = step
        self.unit_of_measurement = unit_of_measurement
        self.device_class = device_class
        self.mode = mode
        self.suggested_display_precision = suggested_display_precision
        self.callback = callback
        self.command_topic = f'{self.topic_prefix}/command'

    def _command_callback(self, client: Client, userdata, message: MQTTMessageInfo) -> None:
        payload = message.payload.decode(errors='replace')
        try:
            new_state = float(payload)
        except ValueError:
            # Home Assistant sends numbers, but this topic is open to anything on the
            # broker, and a heat pump setting is the wrong place to take a guess.
            logger.error(f'{self.name} received {payload!r}, which is not a number')
            return

        self.callback(client=client, component=self, old_state=self.state, new_state=new_state)

    def publish_config(self, client: Client) -> MQTTMessageInfo | None:
        info = super().publish_config(client)

        client.message_callback_add(self.command_topic, self._command_callback)
        result, _ = client.subscribe(self.command_topic)
        if result is not MQTT_ERR_SUCCESS:
            logger.error(f'Error subscribing {self.command_topic=}: {result=}')

        return info

    def validate_state(self, state) -> None:
        super().validate_state(state)

        if not isinstance(state, (int, float)) or isinstance(state, bool):
            raise InvalidStateValue(component=self, error_msg=f'{state=} is not a number')
        if not self.min_value <= state <= self.max_value:
            raise InvalidStateValue(
                component=self, error_msg=f'{state=} is outside {self.min_value} ... {self.max_value}'
            )

    def get_state(self) -> ComponentState:
        return ComponentState(topic=f'{self.topic_prefix}/state', payload=self.state)

    def get_config(self) -> ComponentConfig:
        payload = {
            'component': self.component,
            'device': self.device.get_mqtt_payload(),
            'name': self.name,
            'unique_id': self.uid,
            'state_topic': f'{self.topic_prefix}/state',
            'command_topic': self.command_topic,
            'json_attributes_topic': f'{self.topic_prefix}/attributes',
            'min': self.min_value,
            'max': self.max_value,
            'step': self.step,
            'mode': self.mode,
        }
        if self.unit_of_measurement:
            payload['unit_of_measurement'] = self.unit_of_measurement
        if self.device_class:
            payload['device_class'] = self.device_class
        if self.suggested_display_precision is not None:
            payload['suggested_display_precision'] = self.suggested_display_precision
        return ComponentConfig(topic=f'{self.topic_prefix}/config', payload=payload)

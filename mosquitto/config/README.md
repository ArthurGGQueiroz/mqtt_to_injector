### Para criar usuário do broker MQTT com senha hasheada, no docker, rode:

docker run --rm -it -v $(pwd)/mosquitto/config:/mosquitto/config eclipse-mosquitto mosquitto_passwd -c /mosquitto/config/passwd seu_usuario

- Ao executar o comando, o terminal solicitará a senha duas vezes. Ela será salva já hasheada dentro do arquivo passwd.

### Para adicionar usuário posteriormente, execute:

docker run --rm -it -v $(pwd)/mosquitto/config:/mosquitto/config eclipse-mosquitto mosquitto_passwd /mosquitto/config/passwd outro_usuario

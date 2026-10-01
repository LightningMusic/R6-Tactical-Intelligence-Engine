# Filled in at build time by scripts/embed_client_credentials.py (--voice)
# from the server's server_config.json, then reset to these placeholders so
# the key never sits in the source tree. A build with them empty asks for
# the server address and key on first run instead.
SERVER_URL = ''
VOICE_TOKEN = ''

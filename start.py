import os
import threading
from waitress import serve
from app import app, init, worker

if __name__ == '__main__':
    init()
    threading.Thread(target=worker, daemon=True).start()
    serve(app, host='0.0.0.0', port=int(os.environ.get('PORT', '10000')), threads=4)

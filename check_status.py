import sys; sys.stdout.reconfigure(encoding='utf-8')
from pathlib import Path
t = Path('static/index.html').read_text(encoding='utf-8')
for c in ['quill.snow.css', 'quill.min.js', ':root {', 'publishEditor', 'initPublishEditor', 'updatePublishCharCount', 'togglePublishPreview', 'clearPublishEditor', '_publishEditor', 'Quill(']:
    print(f'  {c}: {"FOUND" if c in t else "MISSING"}')

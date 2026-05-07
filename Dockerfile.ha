FROM homeassistant/home-assistant:${HA_VERSION:-stable}

# Install forked py-aosmith with endpoint failover and stale session recovery
COPY py_aosmith/ /tmp/py-aosmith/py_aosmith/
COPY pyproject.toml README.md /tmp/py-aosmith/
RUN pip3 install --no-cache-dir --no-deps /tmp/py-aosmith && rm -rf /tmp/py-aosmith

# Patch the built-in aosmith integration manifest to not enforce the PyPI version.
# Without this, HA would see py-aosmith 1.1.2 != the pinned ==1.0.15 and downgrade it.
RUN python3 -c "\
import json, importlib.util; \
spec = importlib.util.find_spec('homeassistant'); \
from pathlib import Path; \
manifest = Path(spec.submodule_search_locations[0]) / 'components' / 'aosmith' / 'manifest.json'; \
m = json.loads(manifest.read_text()); \
m['requirements'] = []; \
manifest.write_text(json.dumps(m, indent=2))"

# Increase A.O. Smith polling interval from 30s to 300s to reduce API errors
RUN sed -i 's/REGULAR_INTERVAL = timedelta(seconds=30)/REGULAR_INTERVAL = timedelta(seconds=300)/' \
    /usr/src/homeassistant/homeassistant/components/aosmith/const.py

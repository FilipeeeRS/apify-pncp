FROM apify/actor-python:3.13

COPY --chown=myuser:myuser requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && pip freeze

COPY --chown=myuser:myuser pncp_monitor ./pncp_monitor
RUN python -m compileall -q pncp_monitor/

CMD ["python", "-m", "pncp_monitor"]

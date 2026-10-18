FROM python:3.11-slim
WORKDIR /app
COPY server.py index.html app_data.json ./
ENV PORT=7860
EXPOSE 7860
CMD ["python", "server.py", "--no-browser"]

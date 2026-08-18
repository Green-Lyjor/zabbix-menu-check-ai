#!/usr/bin/env python3
"""Consulta uma LLM pelo OpenRouter para analisar um problema do Zabbix.

Entrada: um objeto JSON como primeiro argumento ou pela entrada padrão.
Exemplo: {"api_key": "...", "model": "openai/gpt-4o-mini", "alert_subject": "..."}
"""

import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_MODEL = "openai/gpt-4o-mini"
DEFAULT_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"


def load_dotenv() -> None:
    """Carrega variáveis simples do arquivo .env ao lado deste script."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.isfile(env_path):
        return

    with open(env_path, encoding="utf-8") as env_file:
        for line in env_file:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            name = name.strip()
            value = value.strip().strip("\"'")
            if name and name not in os.environ:
                os.environ[name] = value


def read_payload() -> dict:
    """Lê os parâmetros do Zabbix como JSON."""
    raw_payload = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read()
    if not raw_payload.strip():
        raise ValueError("Nenhum JSON foi recebido do Zabbix.")

    payload = json.loads(raw_payload)
    if not isinstance(payload, dict):
        raise ValueError("A entrada deve ser um objeto JSON.")
    return payload


def build_prompt(payload: dict) -> str:
    """Monta um contexto curto e útil para diagnóstico do evento."""
    alert_subject = str(payload.get("alert_subject", "")).strip()
    if not alert_subject:
        raise ValueError('O parâmetro "alert_subject" é obrigatório.')

    context_fields = (
        ("Host", "host"),
        ("Nome do trigger", "trigger_name"),
        ("Severidade", "severity"),
        ("Detalhes do alerta", "alert_body"),
        ("Dados operacionais", "operational_data"),
    )
    context = [f"Assunto: {alert_subject}"]
    context.extend(
        f"{label}: {payload[key]}"
        for label, key in context_fields
        if payload.get(key) not in (None, "")
    )

    return "\n".join(
        [
            "Você é um especialista em operações e monitoramento Zabbix.",
            "Analise o evento abaixo e responda em português brasileiro.",
            *context,
            "Forneça no máximo 10 linhas com:",
            "1. possíveis causas, separando fatos de hipóteses;",
            "2. comandos ou verificações seguros para diagnóstico;",
            "3. medidas de mitigação e prevenção.",
            "Não invente dados que não estejam no evento e não recomende ações destrutivas.",
        ]
    )


def request_openrouter(api_key: str, prompt: str, model: str, timeout: int) -> str:
    """Envia o prompt ao OpenRouter e extrai o primeiro texto da resposta."""
    url = os.environ.get("OPENROUTER_ENDPOINT", DEFAULT_ENDPOINT)
    request_body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    site_url = os.environ.get("OPENROUTER_SITE_URL", "").strip()
    site_name = os.environ.get("OPENROUTER_SITE_NAME", "Zabbix").strip()
    if site_url:
        headers["HTTP-Referer"] = site_url
    if site_name:
        headers["X-Title"] = site_name

    request = Request(
        url,
        data=json.dumps(request_body).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            response_body = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        if error.code == 429:
            raise RuntimeError(
                "O OpenRouter recusou a solicitação por limite de uso (HTTP 429)."
            ) from error
        raise RuntimeError(f"O OpenRouter retornou HTTP {error.code}.") from error
    except URLError as error:
        raise RuntimeError(f"Não foi possível acessar o OpenRouter: {error.reason}") from error
    except TimeoutError as error:
        raise RuntimeError("A solicitação ao OpenRouter excedeu o tempo limite.") from error
    except json.JSONDecodeError as error:
        raise RuntimeError("O OpenRouter retornou uma resposta que não é JSON.") from error

    choices = response_body.get("choices", [])
    if not choices:
        error_message = response_body.get("error", {}).get("message", "resposta vazia")
        raise RuntimeError(f"O OpenRouter não retornou uma resposta utilizável: {error_message}.")

    answer = choices[0].get("message", {}).get("content", "").strip()
    if not answer:
        raise RuntimeError("O OpenRouter retornou uma resposta sem texto.")
    return answer


def main() -> int:
    try:
        load_dotenv()
        payload = read_payload()
        api_key = str(
            payload.get(
                "api_key",
                payload.get("openrouter_api_key", os.environ.get("OPENROUTER_API_KEY", "")),
            )
        ).strip()
        if not api_key:
            raise ValueError(
                'O parâmetro "api_key" ou a variável OPENROUTER_API_KEY é obrigatório.'
            )

        timeout = int(os.environ.get("OPENROUTER_TIMEOUT", "30"))
        model = str(payload.get("model", os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL))).strip()
        if not model:
            raise ValueError('O parâmetro "model" não pode ser vazio.')
        print(request_openrouter(api_key, build_prompt(payload), model, timeout))
        return 0
    except (ValueError, TypeError, RuntimeError) as error:
        print(f"OpenRouter Zabbix: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
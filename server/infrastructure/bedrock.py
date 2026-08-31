import boto3
import json
import os
from functools import lru_cache


@lru_cache()
def get_bedrock_client():
    return boto3.client(
        "bedrock-runtime", region_name=os.getenv("AWS_REGION", "us-east-1")
    )


def invoke_claude(prompt: str, system: str = "", max_tokens: int = 1024) -> str:
    client = get_bedrock_client()
    model_id = os.getenv("BEDROCK_CLAUDE_MODEL", "us.anthropic.claude-sonnet-4-6")
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        body["system"] = system
    response = client.invoke_model(modelId=model_id, body=json.dumps(body))
    result = json.loads(response["body"].read())
    return result["content"][0]["text"]


def get_embedding(text: str) -> list[float]:
    client = get_bedrock_client()
    model_id = os.getenv("BEDROCK_EMBEDDING_MODEL", "amazon.titan-embed-text-v2:0")
    response = client.invoke_model(
        modelId=model_id, body=json.dumps({"inputText": text})
    )
    result = json.loads(response["body"].read())
    return result["embedding"]

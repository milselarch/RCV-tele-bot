import yaml
import hashlib
import hmac
import json
import argparse
import uvicorn
from starlette.responses import JSONResponse

from database import *
from load_config import *
from urllib.parse import parse_qs
from fastapi import FastAPI, APIRouter, UploadFile, HTTPException
from pydantic import BaseModel, field_validator, validator
from typing import List, Optional
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from urllib.parse import parse_qs, unquote

TELEGRAM_DATA_HEADER = 'telegram-data'


class FetchPollPayload(BaseModel):
    poll_id: int


class VoteRequestPayload(BaseModel):
    poll_id: int
    votes: List[int]


class VerifyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        telegram_data_header = request.headers.get(TELEGRAM_DATA_HEADER)
        if not telegram_data_header:
            content = {'detail': 'Missing telegram-data header'}
            return JSONResponse(content=content, status_code=401)

        user_params = self.check_authorization(
            telegram_data_header, TELEGRAM_BOT_TOKEN
        )

        if user_params is None:
            content = {'detail': 'Unauthorized'}
            return JSONResponse(content=content, status_code=401)

        request.state.user = user_params
        return await call_next(request)

    @classmethod
    def parse_auth_string(cls, init_data: str):
        params = parse_qs(init_data)
        signature = params.get('hash', [None])[0]
        if signature is None:
            return None

        del params['hash']
        sorted_params = sorted(params.items())
        data_check_string = "\n".join(f"{k}={v[0]}" for k, v in sorted_params)
        return data_check_string, signature, params

    @classmethod
    def check_authorization(
        cls, init_data: str, bot_token: str
    ) -> Optional[dict]:
        parse_result = cls.parse_auth_string(init_data)
        data_check_string, signature, params = parse_result
        secret_key = hashlib.sha256(bot_token.encode()).digest()

        validation_hash = hmac.new(
            secret_key, data_check_string.encode(), hashlib.sha256
        ).hexdigest()

        if validation_hash == signature:
            return {k: v[0] for k, v in params.items()}

        return None


class VotingWebApp(object):
    def __init__(self):
        self.router = APIRouter()
        self.router.add_api_route(
            '/fetch_poll', self.fetch_poll, methods=['POST']
        )
        self.router.add_api_route(
            '/vote', self.cast_vote, methods=['POST']
        )

    def fetch_poll(self, request: Request, payload: FetchPollPayload):
        telegram_data_header = request.headers.get(TELEGRAM_DATA_HEADER)
        parsed_query = parse_qs(telegram_data_header)
        user_json_str = unquote(parsed_query['user'][0])
        user_info = json.loads(user_json_str)
        username = user_info['username']



        raise NotImplementedError

    def cast_vote(self, payload: VoteRequestPayload):
        raise NotImplementedError


app = FastAPI()
predictor = VotingWebApp()
app.include_router(predictor.router)
app.add_middleware(VerifyMiddleware)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='voting web app')
    parser.add_argument(
        '--host', type=str,
        default='0.0.0.0', help='web app host'
    )
    parser.add_argument(
        '--port', type=int,
        default=8080, help='web app port'
    )

    parse_args = parser.parse_args()
    uvicorn.run(app, host=parse_args.host, port=parse_args.port)

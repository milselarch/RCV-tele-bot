import hashlib
import hmac
import json
import argparse
import uvicorn
import dataclasses

from database import *
from load_config import *
from BaseLoader import BaseLoader
from fastapi import FastAPI, APIRouter, UploadFile, HTTPException
from pydantic import BaseModel, field_validator, validator
from typing import List, Optional
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from urllib.parse import parse_qs, unquote, urlencode, quote_plus
from starlette.responses import JSONResponse

TELEGRAM_DATA_HEADER = 'telegram-data'


class FetchPollPayload(BaseModel):
    poll_id: int


class VoteRequestPayload(BaseModel):
    poll_id: int
    votes: List[int]


class VerifyMiddleware(BaseHTTPMiddleware):
    @staticmethod
    async def dev_dispatch(request: Request, call_next):
        """
        convert url GET params into telegram-data request
        header before passing request along to handler in dev mode
        """
        query_params = dict(request.query_params)
        encoded_params = "&".join(
            f"{key}={quote_plus(value)}"
            for key, value in query_params.items()
        )

        custom_headers = dict(request.headers)
        custom_headers['telegram-data'] = encoded_params.encode('utf-8')
        request.scope['headers'] = [
            (k.encode('utf-8'), v) for k, v in custom_headers.items()
        ]

        response = await call_next(request)
        return response

    async def dispatch(self, request: Request, call_next):
        query_params = dict(request.query_params)

        if not PRODUCTION_MODE and 'auth_bypass' in query_params:
            return await self.dev_dispatch(
                request=request, call_next=call_next
            )

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


class VotingWebApp(BaseLoader):
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

        chat_username = user_info['username']
        read_poll_result = self.read_poll_info(
            poll_id=payload.poll_id, chat_username=chat_username
        )

        if read_poll_result.is_err():
            error = read_poll_result.err()
            return JSONResponse(
                status_code=500, content={'error': error.get_content()}
            )

        poll_info = read_poll_result.ok()
        return dataclasses.asdict(poll_info)

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
        default=5010, help='web app port'
    )

    parse_args = parser.parse_args()
    uvicorn.run(app, host=parse_args.host, port=parse_args.port)

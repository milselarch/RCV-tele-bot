import axios, { AxiosResponse } from 'axios';
import {ReactComponent as Logo} from './logo.svg';
import './App.css';
import * as url from "url";
import {useEffect, useState} from "react";
import {BACKEND_URL} from "./config";
import ReactLoading from 'react-loading';

// import { MainButton, useShowPopup } from '@vkruglikov/react-telegram-web-app';

axios.defaults.headers.common['Telegram-Data'] = (
    window?.Telegram?.WebApp?.initData
);

const load_tele_headers = () => {
  let headers = window?.Telegram?.WebApp?.initData ?? '';

  if ((process.env.NODE_ENV === "development") && (headers === '')) {
    console.log("Development mode");
    headers = window.location.search;
  } else {
    console.log("Production mode");
  }

  // console.log('INIT_DATA', initData)
  return headers
}

const fetch_poll = async (poll_id: number) => {
  const endpoint = `${BACKEND_URL}/fetch_poll`;
  console.log('ENDPOINT', endpoint, poll_id)

  const request = axios.post(
      endpoint, {'poll_id': poll_id},
      {headers: {'Content-Type': 'application/json'}}
  )

  let response: AxiosResponse<any, any>;

  try {
    response = await request
  } catch (error) {
    if (axios.isAxiosError(error)) {
      console.error('Axios error:', error.toJSON());
    } else {
      console.error('Unexpected error:', error);
    }
  }

  return response
}

const Content = ({
   authenticated
}: {
  authenticated: boolean
}) => {
  console.log('AUTHENTICATED', authenticated)

  if (!authenticated) {
    return <p> User not authenticated </p>
  } else {
    return <p> authenticated </p>
  }
}

interface Poll {
  poll_options: Array<string>,
  poll_title: string,
  poll_id: number
}

function App() {
  /*
  const showPopup = useShowPopup();
  const handleClick = () => showPopup({
    message: 'Hello, I am popup',
  });
  */

  const [headers, set_headers] = useState('');
  const [has_credential, set_has_credential] = useState(false);
  const [poll, set_poll] = useState(null)

  useEffect(() => {
    const headers = load_tele_headers()
    const has_credential = headers !== ''

    set_has_credential(has_credential);
    set_headers(headers);

    if (has_credential) {
      axios.defaults.headers.common['telegram-data'] = headers;
    }

    const query_params = {};
    const params = new URLSearchParams(window.location.search);
    params.forEach((value, key) => { query_params[key] = value; });

    const poll_id = Number.parseInt(query_params['poll_id'])
    if (poll_id === null) {
      throw 'NO POLL ID SPECIFIED'
    }

    fetch_poll(poll_id).then((response) => {
        console.log('AXIOS RESPONSE', response)
        const poll: Poll = {
          'poll_options': response['poll_options'],
          'poll_title': response['poll_question'],
          'poll_id': poll_id
        }

        set_poll(poll);
    });
  }, [])

  return (
    <div className="App">
      <header className="App-header">
        <ReactLoading type="spin" height={'10rem'} width={'10rem'}/>
        <Content authenticated={has_credential}/>
      </header>
    </div>
  );
}

export default App;

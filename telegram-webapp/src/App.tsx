import axios, { AxiosResponse } from 'axios';
// import {ReactComponent as Logo} from './logo.svg';
import './App.scss';
import {useEffect, useState} from "react";
import {BACKEND_URL} from "./config";
import ReactLoading from 'react-loading';

// import { MainButton, useShowPopup } from '@vkruglikov/react-telegram-web-app';

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
    endpoint, {'poll_id': poll_id}, {
      headers: {'Content-Type': 'application/json'},
      timeout: 5000 // time out after 5 seconds
    }
  )

  const response = await request
  console.log('ENDPOINT RESPONSE', response)
  return response
}

const Content = ({
   authenticated, poll
}: {
  authenticated: boolean, poll: Poll | null
}) => {
  console.log('AUTHENTICATED', authenticated)

  let render_items: Array<JSX.Element> = []
  if (poll !== null) {
    console.log('POLL', poll)
    render_items = poll.poll_options.map((option, index) => (
      <div key={index} className="poll-option">
        <p className="index">{index+1}.</p>
        <p className="option">{option}</p>
      </div>
    ));
  }

  if (!authenticated) {
    return null
  } else {
    return (
      <div className="poll-container">
        <div className="poll-options">
          {render_items.map((render_item) => ( render_item ))}
        </div>
      </div>
    )
  }
}

interface Poll {
  poll_options: Array<string>,
  poll_title: string,
  poll_id: number
}

const StatusLoader = ({
  loading, status
}: {loading: boolean, status: string | null}) => {
  if (status === null) {
    return null
  } else if (loading) {
    return (
      <div>
        <p>{status}</p>
        <ReactLoading type="spin" height={'10rem'} width={'10rem'}/>
      </div>
    )
  } else {
    return (
      <div>
        <p>{status}</p>
      </div>
    )
  }
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
  const [poll, set_poll] = useState<Poll | null>(null)
  const [vote_rankings, set_vote_rankings] = useState<Array<number>>([])
  const [loading, set_loading] = useState(false)
  const [status, set_status] = useState<string>(null)

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

    set_loading(true)
    set_status('loading')

    fetch_poll(poll_id).then((response) => {
      if (response === null) { throw 'REQUEST FAILED' }
      console.log('AXIOS RESPONSE', response)
      const poll: Poll = response.data;
      set_status(null)
      set_poll(poll)

    }).catch((error) => {
      if (axios.isAxiosError(error)) {
        console.error('Axios error:', error.toJSON());
        if (error.code === "ECONNABORTED") {
          set_status('Connection timed out')
        } else {
          set_status('Server request failed')
        }
      } else {
        console.error('Unexpected error:', error);
        set_status('Unexpected error')
      }

    }).finally(() => {
      set_loading(false)
    });
  }, [])

  return (
    <div className="App">
      <header className="App-header">
        <StatusLoader loading={loading} status={status} />
        <Content authenticated={has_credential} poll={poll}/>
      </header>
    </div>
  )
}

export default App;


import axios from 'axios';
import { ReactComponent as Logo } from './logo.svg';
import './App.css';
import * as url from "url";
import {useEffect, useState} from "react";

// import { MainButton, useShowPopup } from '@vkruglikov/react-telegram-web-app';

axios.defaults.headers.common['Telegram-Data'] = (
    window?.Telegram?.WebApp?.initData
);

/*
const Content = () => {
  const showPopup = useShowPopup();

  const handleClick = () =>
    showPopup({
      message: 'Hello, I amn popup',
    });

  return <MainButton text="SHOW POPUP" onClick={handleClick} />;
};
*/

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

const Content = ({authenticated}: {authenticated: boolean}) => {
  console.log('AUTHENTICATED', authenticated)

  if (!authenticated) {
    return <p> User not authenticated </p>
  } else {
    return <p> authenticated </p>
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

  useEffect(() => {
    const loaded_headers = load_tele_headers()
    set_has_credential(loaded_headers !== '');
    set_headers(loaded_headers);

    if (has_credential) {
      axios.defaults.headers.common['telegram-data'] = headers;
    }
  }, [])

  return (
    <div className="App">
      <header className="App-header">
        <Logo className="App-logo" />
        <Content authenticated={has_credential}/>
      </header>
    </div>
  );
}

export default App;

import axios from 'axios';
import { ReactComponent as Logo } from './logo.svg';
import './App.css';
import * as url from "url";

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

  if (process.env.NODE_ENV === "development") {
    console.log("Development mode");
    if (!window.Telegram) {
      window.Telegram = { 'WebApp': { 'initData': window.location.search } }
    }
  } else {
    console.log("Production mode");
  }

  const { initData } = window?.Telegram?.WebApp ?? {};
  const has_credential = initData !== '';
  console.log('INIT_DATA', [initData]);

  if (has_credential) {
    axios.defaults.headers.common['telegram-data'] = initData;

  }

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

import React from 'react';

import axios from 'axios';
import { ReactComponent as Logo } from './logo.svg';
import './App.css';

import { MainButton, useShowPopup } from '@vkruglikov/react-telegram-web-app';

axios.defaults.headers.common['Telegram-Data'] = (
    window?.Telegram?.WebApp?.initData
);

const Content = () => {
  const showPopup = useShowPopup();

  const handleClick = () =>
    showPopup({
      message: 'Hello, I amn popup',
    });

  return <MainButton text="SHOW POPUP" onClick={handleClick} />;
};

function App() {
  const showPopup = useShowPopup();

  const handleClick = () =>
    showPopup({
      message: 'Hello, I am popup',
    });

  const { initData } = window?.Telegram?.WebApp ?? {};

  return (
    <div className="App">
      <header className="App-header">
        <Logo className="App-logo" />
        <p>
          Edit <code>src/App.js</code> and save to reload.
          <code> --- {[initData]} --- </code>
        </p>
        <Content/>
        <a
          className="App-link"
          href="https://reactjs.org"
          target="_blank"
          rel="noopener noreferrer"
        >
          Learn React
        </a>

        <MainButton text="SHOW POPUP" onClick={handleClick} />;
      </header>
    </div>
  );
}

export default App;

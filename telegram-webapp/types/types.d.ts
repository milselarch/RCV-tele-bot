declare module '*.svg' {
  import React = require('react');
  export const ReactComponent: React.FunctionComponent<React.SVGProps<SVGSVGElement>>;
  const src: string;
  export default src;
}

interface Window {
  Telegram: {
    WebApp: {
      initData: string | null;
      sendData: (json_data: string) => void;
    };
  };
}
import {TelegramWebApp} from "@vkruglikov/react-telegram-web-app/lib/core/twa-types/WebApp";

function hexToRgb(hex: string): [number, number, number] | null {
  hex = hex.replace(/^#/, '');
  // Expand shorthand form (#fff) to full form (#ffffff)
  if (hex.length === 3) {
    hex = hex.split('').map(x => x + x).join('');
  }

  if (hex.length !== 6) return null;
  const num = parseInt(hex, 16);
  const r = (num >> 16) & 255;
  const g = (num >> 8) & 255;
  const b = num & 255;

  return [r, g, b];
}

export function applyThemeParams(themeParams: TelegramWebApp.ThemeParams) {
  const root = document.documentElement;

  for (const [key, value] of Object.entries(themeParams)) {
    // console.log(`Processing theme param: ${key} = ${value}`);
    if (!value) { continue }
    if (!key.endsWith('color')) { continue }

    const scssVarName = key.replace(/_/g, '-');
    const rgb = hexToRgb(value);
    if (rgb === null) { continue }

    const rgbString = `${rgb[0]}, ${rgb[1]}, ${rgb[2]}`
    root.style.setProperty(`--tg-${scssVarName}`, value);
    root.style.setProperty(`--tg-${scssVarName}-rgb`, rgbString);
    // console.log(`Set CSS variable --tg-${scssVarName} to ${value} (${rgbString})`);
  }
}

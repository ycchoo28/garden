// The app's PrimeVue theme: Aura with the petrol accent, our neutrals and a visible focus ring.
// Every colour a component shows comes from here. Screens never colour a PrimeVue component themselves.
import { definePreset } from '@primevue/themes';
import Aura from '@primevue/themes/aura';

// White text on 600 is 5.4:1 and 700 on white is 7.3:1, so both pass WCAG AA.
const petrol = {
  50: '#effafa',
  100: '#d6f1f2',
  200: '#b0e2e5',
  300: '#7bcbd1',
  400: '#45abb5',
  500: '#278f9b',
  600: '#1f7482',
  700: '#1d5e6b',
  800: '#1e4e59',
  900: '#1d424c',
  950: '#0e2a32',
};

export const GardenPreset = definePreset(Aura, {
  primitive: { petrol },
  semantic: {
    primary: {
      50: '{petrol.50}',
      100: '{petrol.100}',
      200: '{petrol.200}',
      300: '{petrol.300}',
      400: '{petrol.400}',
      500: '{petrol.500}',
      600: '{petrol.600}',
      700: '{petrol.700}',
      800: '{petrol.800}',
      900: '{petrol.900}',
      950: '{petrol.950}',
    },
    focusRing: { width: '2px', style: 'solid', color: '{primary.color}', offset: '2px', shadow: 'none' },
    colorScheme: {
      light: {
        surface: {
          0: '#ffffff',
          50: '#f5f6f8',
          100: '#eef0f3',
          200: '#e1e5ea',
          300: '#c7cdd6',
          400: '#9aa3b0',
          500: '#7a8494',
          600: '#5b6678',
          700: '#3c4555',
          800: '#2a313b',
          900: '#1b2230',
          950: '#0d1117',
        },
        primary: {
          color: '{primary.600}',
          contrastColor: '#ffffff',
          hoverColor: '{primary.700}',
          activeColor: '{primary.800}',
        },
        highlight: {
          background: '{primary.50}',
          focusBackground: '{primary.100}',
          color: '{primary.800}',
          focusColor: '{primary.900}',
        },
        text: {
          color: '{surface.900}',
          hoverColor: '{surface.950}',
          mutedColor: '{surface.600}',
          hoverMutedColor: '{surface.700}',
        },
        formField: {
          color: '{surface.900}',
          borderColor: '{surface.300}',
          hoverBorderColor: '{surface.400}',
        },
      },
      dark: {
        surface: {
          0: '#ffffff',
          50: '#e6e9ee',
          100: '#c9cfd8',
          200: '#9aa4b2',
          300: '#7d8796',
          400: '#5f6978',
          500: '#3a4350',
          600: '#2a313b',
          700: '#252c36',
          800: '#1c222b',
          900: '#161b22',
          950: '#0d1117',
        },
        primary: {
          color: '{primary.300}',
          contrastColor: '{primary.950}',
          hoverColor: '{primary.200}',
          activeColor: '{primary.100}',
        },
        highlight: {
          background: 'color-mix(in srgb, {primary.300}, transparent 84%)',
          focusBackground: 'color-mix(in srgb, {primary.300}, transparent 76%)',
          color: '{primary.100}',
          focusColor: '{primary.50}',
        },
        text: {
          color: '{surface.50}',
          hoverColor: '{surface.0}',
          mutedColor: '{surface.200}',
          hoverMutedColor: '{surface.100}',
        },
        content: {
          borderColor: '{surface.600}',
        },
        formField: {
          background: '{surface.950}',
          color: '{surface.50}',
          borderColor: '{surface.500}',
          hoverBorderColor: '{surface.400}',
        },
      },
    },
  },
});

// Dark mode is the `app-dark` class on <html>, which colorMode.ts and public/color-mode.js set.
// PrimeVue's layer comes before Tailwind's utilities (see styles.css), so utilities win without !important.
export const themeOptions = {
  preset: GardenPreset,
  options: {
    darkModeSelector: '.app-dark',
    cssLayer: { name: 'primevue', order: 'theme, base, primevue' },
  },
};

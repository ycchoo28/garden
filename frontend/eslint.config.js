import js from '@eslint/js';
import boundaries from 'eslint-plugin-boundaries';
import pluginVue from 'eslint-plugin-vue';
import globals from 'globals';
import tseslint from 'typescript-eslint';
import vueParser from 'vue-eslint-parser';

export default tseslint.config(
  { ignores: ['dist/', 'src/api/generated/', 'playwright-report/', 'test-results/'] },
  js.configs.recommended,
  tseslint.configs.strict,
  ...pluginVue.configs['flat/recommended'],
  {
    files: ['**/*.{ts,vue}'],
    languageOptions: {
      globals: globals.browser,
      parser: vueParser,
      parserOptions: { parser: tseslint.parser, extraFileExtensions: ['.vue'] },
    },
  },
  {
    // Plain scripts the page loads before the app, such as the colour mode set before the first paint.
    files: ['public/**/*.js'],
    languageOptions: { globals: globals.browser, sourceType: 'script' },
  },
  {
    files: ['src/**/*.{ts,vue}'],
    plugins: { boundaries },
    settings: {
      'import/resolver': { node: { extensions: ['.ts', '.vue', '.js'] } },
      'boundaries/elements': [
        { type: 'api', pattern: 'src/api/**' },
        { type: 'shared', pattern: 'src/shared/**' },
        { type: 'core', pattern: 'src/core/*/**', capture: ['core'] },
        { type: 'module', pattern: 'src/modules/*/**', capture: ['module'] },
        { type: 'application', pattern: 'src/app/**' },
        { type: 'entry', pattern: 'src/*' },
      ],
    },
    rules: {
      'boundaries/dependencies': [
        'error',
        {
          default: 'disallow',
          policies: [
            {
              from: { element: { type: 'entry' } },
              allow: { to: { element: { type: ['application', 'shared'] } } },
            },
            {
              from: { element: { type: 'application' } },
              allow: { to: { element: { type: ['api', 'shared', 'core', 'module', 'application'] } } },
            },
            {
              from: { element: { type: 'core' } },
              allow: [
                { to: { element: { type: ['api', 'shared'] } } },
                {
                  to: {
                    element: { type: 'core', captured: { core: '{{from.element.captured.core}}' } },
                  },
                },
              ],
            },
            {
              from: { element: { type: 'module' } },
              allow: [
                { to: { element: { type: ['api', 'shared'] } } },
                {
                  to: {
                    element: { type: 'module', captured: { module: '{{from.element.captured.module}}' } },
                  },
                },
              ],
            },
            {
              from: { element: { type: 'shared' } },
              allow: { to: { element: { type: ['api', 'shared'] } } },
            },
            {
              from: { element: { type: 'api' } },
              allow: { to: { element: { type: 'api' } } },
            },
          ],
        },
      ],
    },
  },
);

import js from '@eslint/js';
import boundaries from 'eslint-plugin-boundaries';
import pluginVue from 'eslint-plugin-vue';
import globals from 'globals';
import tseslint from 'typescript-eslint';
import vueParser from 'vue-eslint-parser';

const gardenRules = {
  rules: {
    'no-server-calls': {
      meta: {
        type: 'problem',
        docs: { description: 'Require server requests to use the generated API client.' },
        schema: [],
        messages: {
          useClient: 'Use the generated API client for server calls.',
        },
      },
      create(context) {
        const source = context.sourceCode;
        const member = (node, object, property) =>
          node?.type === 'MemberExpression' &&
          node.object?.name === object &&
          (node.computed ? node.property.value : node.property.name) === property;
        const urlPattern = /^(?:https?:\/\/|\/api\/)/i;
        return {
          CallExpression(node) {
            const callee = node.callee;
            if (
              (callee.type === 'Identifier' && ['fetch', 'axios'].includes(callee.name)) ||
              ['fetch', 'globalThis', 'self', 'window'].some((object) => member(callee, object, 'fetch')) ||
              (callee.type === 'MemberExpression' && callee.object?.name === 'axios')
            ) {
              context.report({ node, messageId: 'useClient' });
            }
          },
          NewExpression(node) {
            const callee = node.callee;
            if (
              callee.type === 'Identifier' && ['XMLHttpRequest', 'WebSocket'].includes(callee.name) ||
              ['XMLHttpRequest', 'WebSocket'].some((name) => member(callee, 'window', name))
            ) {
              context.report({ node, messageId: 'useClient' });
            }
          },
          Literal(node) {
            if (typeof node.value === 'string' && urlPattern.test(node.value)) {
              context.report({ node, messageId: 'useClient' });
            }
          },
          TemplateElement(node) {
            if (urlPattern.test(source.getText(node).replace(/^`|`$/g, ''))) {
              context.report({ node, messageId: 'useClient' });
            }
          },
        };
      },
    },
  },
};

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
    plugins: { boundaries, garden: gardenRules },
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
              allow: { to: { element: { type: 'shared' } } },
            },
            {
              from: { element: { type: 'api' } },
              allow: { to: { element: { type: 'api' } } },
            },
          ],
        },
      ],
      'garden/no-server-calls': 'error',
    },
  },
  {
    files: ['src/app/**/*.{ts,vue}'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            { group: ['../modules/*/*', '../core/*/*', '@/modules/*/*', '@/core/*/*'], message: 'Import package public entry points only.' },
          ],
        },
      ],
    },
  },
);

import fs from 'node:fs';
import path from 'node:path';
import babelParser from '@babel/eslint-parser';
import js from '@eslint/js';
import pluginVue from 'eslint-plugin-vue';
import globals from 'globals';
import vueParser from 'vue-eslint-parser';

const SRC_ROOT = path.resolve(import.meta.dirname, 'src');

function classifySrcPath(filePath) {
  const rel = path.relative(SRC_ROOT, filePath).split(path.sep).join('/');
  if (!rel || rel.startsWith('..') || path.isAbsolute(rel)) return null;
  const parts = rel.split('/');
  if (parts.length === 1) return { type: 'entry' };
  const [group, name] = parts;
  if (group === 'api') return { type: 'api' };
  if (group === 'shared') return { type: 'shared' };
  if (group === 'app') return { type: 'application' };
  if (group === 'core' && parts.length >= 3) return { type: 'core', captured: name };
  if (group === 'modules' && parts.length >= 3) return { type: 'module', captured: name };
  return null;
}

function resolveImportTarget(fromFile, specifier) {
  let candidate;
  if (specifier.startsWith('@/')) {
    candidate = path.resolve(SRC_ROOT, specifier.slice(2));
  } else if (specifier.startsWith('.')) {
    candidate = path.resolve(path.dirname(fromFile), specifier);
  } else {
    return null;
  }
  const ext = path.extname(candidate);
  if (ext && !['.ts', '.tsx', '.vue', '.js'].includes(ext)) {
    return null;
  }
  const probe = [
    candidate,
    `${candidate}.ts`,
    `${candidate}.tsx`,
    `${candidate}.vue`,
    `${candidate}.js`,
    path.join(candidate, 'index.ts'),
    path.join(candidate, 'index.js'),
  ];
  for (const option of probe) {
    if (fs.existsSync(option) && fs.statSync(option).isFile()) {
      return option;
    }
  }
  return candidate;
}

function isAllowedDependency(fromElement, toElement) {
  if (!fromElement || !toElement) return true;
  switch (fromElement.type) {
    case 'entry':
      return toElement.type === 'application' || toElement.type === 'shared';
    case 'application':
      return ['api', 'shared', 'core', 'module', 'application'].includes(toElement.type);
    case 'core':
      return (
        toElement.type === 'api' ||
        toElement.type === 'shared' ||
        (toElement.type === 'core' && toElement.captured === fromElement.captured)
      );
    case 'module':
      return (
        toElement.type === 'api' ||
        toElement.type === 'shared' ||
        (toElement.type === 'module' && toElement.captured === fromElement.captured)
      );
    case 'shared':
      return toElement.type === 'shared';
    case 'api':
      return toElement.type === 'api';
    default:
      return true;
  }
}

const gardenRules = {
  rules: {
    boundaries: {
      meta: {
        type: 'problem',
        docs: { description: 'Enforce frontend layer and module dependency boundaries.' },
        schema: [],
        messages: {
          disallowed:
            'Dependency from "{{from}}" to "{{to}}" is not allowed by frontend module boundaries.',
        },
      },
      create(context) {
        const filename = path.resolve(import.meta.dirname, context.filename);
        const fromElement = classifySrcPath(filename);
        if (!fromElement) return {};

        function checkSpecifier(node, specifier) {
          if (typeof specifier !== 'string') return;
          const targetFile = resolveImportTarget(filename, specifier);
          if (!targetFile) return;
          const toElement = classifySrcPath(targetFile);
          if (!isAllowedDependency(fromElement, toElement)) {
            context.report({
              node,
              messageId: 'disallowed',
              data: { from: fromElement.type, to: toElement.type },
            });
          }
        }

        return {
          ImportDeclaration(node) {
            checkSpecifier(node, node.source.value);
          },
          ExportNamedDeclaration(node) {
            if (node.source) checkSpecifier(node, node.source.value);
          },
          ExportAllDeclaration(node) {
            checkSpecifier(node, node.source.value);
          },
          ImportExpression(node) {
            if (node.source.type === 'Literal') {
              checkSpecifier(node, node.source.value);
            }
          },
        };
      },
    },
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
              ['fetch', 'globalThis', 'self', 'window'].some((object) =>
                member(callee, object, 'fetch'),
              ) ||
              (callee.type === 'MemberExpression' && callee.object?.name === 'axios')
            ) {
              context.report({ node, messageId: 'useClient' });
            }
          },
          NewExpression(node) {
            const callee = node.callee;
            if (
              (callee.type === 'Identifier' && ['XMLHttpRequest', 'WebSocket'].includes(callee.name)) ||
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

export default [
  { ignores: ['dist/', 'src/api/generated/', 'playwright-report/', 'test-results/'] },
  js.configs.recommended,
  ...pluginVue.configs['flat/recommended'],
  {
    files: ['**/*.{ts,vue}'],
    languageOptions: {
      globals: globals.browser,
      parser: vueParser,
      parserOptions: {
        parser: babelParser,
        requireConfigFile: false,
        babelOptions: {
          presets: [['@babel/preset-typescript', { ignoreExtensions: true }]],
        },
        extraFileExtensions: ['.vue'],
      },
    },
    rules: {
      // TypeScript checking (vue-tsc --noEmit) covers unused identifiers and type-only imports.
      'no-unused-vars': 'off',
      'no-undef': 'off',
    },
  },
  {
    files: ['scripts/**/*.mjs'],
    languageOptions: { globals: globals.node },
  },
  {
    // Plain scripts the page loads before the app, such as the colour mode set before the first paint.
    files: ['public/**/*.js'],
    languageOptions: { globals: globals.browser, sourceType: 'script' },
  },
  {
    files: ['src/**/*.{ts,vue}'],
    plugins: { garden: gardenRules },
    rules: {
      'garden/boundaries': 'error',
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
            {
              group: ['../modules/*/*', '../core/*/*', '@/modules/*/*', '@/core/*/*'],
              message: 'Import package public entry points only.',
            },
          ],
        },
      ],
    },
  },
];

import type { ModuleNavigation } from '../../shared/ui';
import { KNOWLEDGE_SEARCH_LOG_PERMISSION, KNOWLEDGE_SEARCH_PERMISSION } from './access';

// The knowledge module's links in the header. The shell hides each link from a person without its permission.
export const knowledgeNavigation: ModuleNavigation = {
  label: 'Knowledge',
  items: [
    { label: 'Search', to: '/search', permission: KNOWLEDGE_SEARCH_PERMISSION },
    { label: 'Search log', to: '/search-log', permission: KNOWLEDGE_SEARCH_LOG_PERMISSION },
  ],
};

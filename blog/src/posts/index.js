import { registerPost } from '../blog-components';

import agentRuntime from './agent-runtime/index.jsx';
import oauthMcp from './oauth-mcp/index.jsx';

[agentRuntime, oauthMcp].forEach(registerPost);

import { registerPost } from '../blog-components';

import kitchenSink from './kitchen-sink/index.jsx';
import quietSignals from './quiet-signals/index.jsx';
import strata from './strata/index.jsx';
import horizon from './horizon/index.jsx';
import rawHtml from './raw-html/index.jsx';
import garden from './garden/index.jsx';

[kitchenSink, quietSignals, strata, horizon, rawHtml, garden].forEach(registerPost);

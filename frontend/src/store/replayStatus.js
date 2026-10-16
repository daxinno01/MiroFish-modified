import { getReplay } from '../api/recordings'
import { sharedPoller } from '../utils/polling'

export const subscribeReplay = sharedPoller(async (id, signal) =>
  (await getReplay(id, { signal })).data)

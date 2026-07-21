import { getModel, type Model } from '@mariozechner/pi-ai'

const dynamicGetModel = getModel as unknown as (
  provider: string,
  modelId: string,
) => Model<string> | undefined

export function resolveModel(provider: string, modelId: string): Model<string> {
  if (!provider.trim() || !modelId.trim()) {
    throw new TypeError('model provider and model ID are required')
  }
  try {
    const model = dynamicGetModel(provider, modelId)
    if (!model) {
      throw new Error('model is not registered')
    }
    return model
  } catch (error) {
    throw new Error(`unknown Pi model ${provider}/${modelId}`, { cause: error })
  }
}

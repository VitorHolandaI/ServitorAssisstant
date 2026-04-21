# get_forecast

Returns current weather conditions.

**Default location:** Campina Grande, Paraíba, Brazil — called with no arguments.  
Pass coordinates only if the user asks about a specific city.

## Parameters

| Name | Type | Required | Default |
|------|------|----------|---------|
| `latitude` | float | no | `-7.2307` |
| `longitude` | float | no | `-35.8816` |

## Returns

```
Temperature: 28.0°C
Humidity: 65%
Rain: 0.0 mm
Wind: 12.3 km/h
```

## Example prompts

- "What's the weather?"
- "Weather in São Paulo?" → model passes SP coordinates

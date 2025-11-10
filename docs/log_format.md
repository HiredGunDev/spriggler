
### **Fields**
1. **TIMESTAMP**: ISO 8601 format timestamp for when the log entry was created.
2. **COMPONENT_TYPE**: The subsystem or functional area generating the log:
    - `sensor`: For sensor updates and issues.
    - `control`: For actions taken on devices like heaters, fans, or humidifiers.
    - `network`: For all communication with the Spriggle server (e.g., configuration updates, state uploads).
    - `system`: For internal system events and daemon-level logs.
3. **ENTITY_NAME**: The specific environment or component associated with the log entry:
    - Examples: `grow_large`, `plenum`, `global`, `grow_large_humidifier`.
4. **LEVEL**: The severity level of the log entry:
    - `INFO`: For routine operations and updates.
    - `WARNING`: For non-critical issues (e.g., sensor timeouts).
    - `ERROR`: For errors that do not halt operations.
    - `CRITICAL`: For high-priority issues that require immediate attention.
5. **MESSAGE**: A human-readable message describing the event.

---

## **Log Examples**

### **Sensor Updates**
- **Description**: Logs sensor readings, including the number of updates received (`U`) since the last entry.
- **Examples**:
    ```
    2024-12-20 20:21:00 - sensor - grow_large - INFO - Sensor Update: U:6 | T: 73.76°F | H: 65.40% | B: 76%
    2024-12-20 20:26:00 - sensor - grow_large - WARNING - No updates received for 300s. Last known state: T: 73.76°F | H: 65.40% | B: 76%
    ```

### **Control Actions**
- **Description**: Logs actions taken on devices like heaters, fans, and humidifiers.
- **Examples**:
    ```
    2024-12-20 20:21:01 - control - grow_large_humidifier - INFO - Action Taken: Humidifier turned off.
    2024-12-20 20:21:02 - control - grow_large_heater - INFO - Action Taken: Heater turned on.
    ```

### **Network Communications**
- **Description**: Logs communication with the Spriggle server, including configuration updates and periodic uploads.
- **Examples**:
    ```
    2024-12-20 20:20:00 - network - global - INFO - Received updated configuration from Spriggle.
    2024-12-20 20:50:00 - network - global - INFO - State sent upstream: {"temperature": {"grow_large": 72.5}, "humidity": {"grow_large": 64.2}, "status": "stable"}
    2024-12-20 20:50:01 - network - global - INFO - Log tranche sent upstream: 25 entries.
    ```

### **System Events**
- **Description**: Logs daemon-level events such as startup, configuration loading, and critical failures.
- **Examples**:
    ```
    2024-12-20 20:20:01 - system - global - INFO - Spriggler daemon started successfully.
    2024-12-20 21:00:01 - system - global - ERROR - Failed to load configuration: FileNotFoundError
    ```

---

## **Severity Levels**

| Level       | Description                                 |
|-------------|---------------------------------------------|
| `INFO`      | Normal operations and routine updates.      |
| `WARNING`   | Non-critical issues requiring attention.     |
| `ERROR`     | Errors that do not halt system operation.    |
| `CRITICAL`  | Severe issues that may impact functionality. |

---

## **Best Practices**

1. **Structured Fields**: Always include `COMPONENT_TYPE` and `ENTITY_NAME` for clarity.
2. **Granular Context**: Use specific `ENTITY_NAME` values (e.g., `grow_large_humidifier`) for actionable logs.
3. **Batch Networking Logs**: Send periodic updates upstream to reduce network overhead.
4. **Filterable**: Use tools like `grep` or log analysis software to filter by `COMPONENT_TYPE` or `ENTITY_NAME`.

---

## **Future Enhancements**
- Add unique request IDs for better traceability across distributed systems.
- Include optional correlation IDs for linking logs across components.

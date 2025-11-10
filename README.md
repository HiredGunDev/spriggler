Spriggler Daemon
================

Spriggler is a daemon designed to manage and monitor grow environments with advanced environmental controls, robust logging, and integration with the Spriggle platform for remote management and monitoring.

* * *

Key Features
------------

*   **Dynamic Device and Sensor Management**: Automatically discovers and manages supported devices and sensors.
*   **Environment Control**: Handles temperature, humidity, and lighting schedules for multiple grow environments.
*   **Logging**: Provides detailed, consistent logs for sensors, controls, network interactions, and system events.
*   **Network Integration**: Communicates with the Spriggle platform for configuration updates, state uploads, and log synchronization.
*   **Fail-Safe Design**: Ensures local environment control even when disconnected from the internet.

* * *

Getting Started
---------------

1.  **Clone the Repository**:

        git clone https://github.com/yourusername/spriggler.git
        cd spriggler

2.  **Install Dependencies**:

        pip install -r requirements.txt

3.  **Run the Daemon**:

        python spriggler.py


* * *

Documentation
-------------

For detailed information about Spriggler, visit the `docs` directory.

### Included Documentation

*   [Log File Format](docs/log_format.md): Describes the structure and purpose of Spriggler's logs.
*   [Configuration](docs/configuration.md): Describes the Spriggler configuration file.
*   [Configuration Schema](docs/configuration_schema.json): Formal JSON schema for the Spriggler configuration file.
*   Additional documentation will be added as the project grows.

* * *

Directory Structure
-------------------

    spriggler/
    ├── spriggler.py       # Main daemon script
    ├── config.json        # Configuration file
    ├── docs/              # Documentation files
    ├── devices/           # Device modules
    ├── sensors/           # Sensor modules
    ├── tests/             # Test scripts
    ├── requirements.txt   # Python dependencies
    └── README.md          # This file

* * *

Contributing
------------

We welcome contributions to Spriggler! If you'd like to add support for additional devices or improve existing features:

1.  Fork the repository.
2.  Create a feature branch.
3.  Submit a pull request.

* * *

License
-------

Spriggler is open-source and licensed under the MIT License. See the `LICENSE` file for details.

* * *

Support
-------

For questions, issues, or feature requests, please open an issue in the GitHub repository.

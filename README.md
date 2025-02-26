# AISstream Vessel Tracker

## Description
This project allows you to track your favorite vessel that is available on the aisstream's API. By inputting your desired coordinates on a certain location, it will automatically find ships neaby within the radius of your pinpointed location. The API is still in beta and is unstable as per the developer's note, more info can be read here [aisstream.io](https://aisstream.io/documentation).

## Table of Contents
- [Installation](#installation)
- [Usage](#usage)
- [Contact](#contact)

## Installation
You can have this by cloning this project by simply doing:

```bash
git clone https://github.com/m3m0rydmp/AISstream-Vessel-Tracker.git
```
Or by downloading it as a zip file.

## Usage
**1st** You need to get an API key first at [aisstream.io](aisstream.io). Create an account and then create an API key.

Inspect the python file to adjust the necessary configuration. At **line 19** you could see the variable for the API key and put your newly created API key from the aisstream.
Then, adjust the coordinates by configuring the bounding boxes found in **line 45**. The nested list of arrays in it are the coordinates, you can use online maps to map the coordinates of your desired location. The bounding box requires two _latitudes_ and two _longitudes_ because it defines a rectangular area on the Earth's surface. Think of it like drawing a box on a map: you need two points to describe its opposite corners—typically the southwest corner (minimum latitude and longitude) and the northeast corner (maximum latitude and longitude).

Run the python file, and it will automatically make the **html** file and the **json** file. This will also automatically run the html file in your browser.

Configure also the **watched_vessels.txt** file to use the filter feature. If you have a list of ship that needs to be filtered, meaning only show that ship based on your list you can put their MMSI numbers within this txt file. Just be sure that the program is terminated in order to make this configurations work.

## WHEN THE PROGRAM IS RUNNING
This program is very slow as it is just a simple program running locally. As there are features such as search, find on map, and filter. The search term is always running every second, by looking at the logs it is reading the inputs from the search bar, the output appears as `search_term=""`. This will read on the json file to find the ship that has been already scanned.

The vessel's visibility is limited only to 5000, but will continously scan for ships. The ships that has been scanned for over 30 minutes will be removed, and will be replaced with new ones. 

When enabling the filter feature, you will notice on the logs that the map update is halted and the search filter is set to _False/True_. Wait for the map update log to appear and then refresh the page so it will be updated.

## Contact
Program made by m3m0rydmp

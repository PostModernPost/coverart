"""Made-up albums for the Play Store screenshots: none of these bands or records exist, so the images
show no one else's artwork. Each has what the screens need: sides and track lengths, credits, a band story,
similar bands, and (for the songs on screen) lyrics written for these images."""

ALBUMS = [
    {
        "id": "bluehour", "title": "Blue Hour Sessions", "artist": "The Lenny Marsh Quartet", "year": "1959",
        "genre": "Jazz", "label": "Meridian Records", "catno": "MR-4012", "medium": "vinyl",
        "sides": {
            "A": [("Blue Hour", "6:42"), ("Lantern Street", "5:18"), ("For Ella, Quietly", "7:05")],
            "B": [("Night Ferry", "6:11"), ("Saint Clair Shuffle", "4:56"), ("Last Set", "8:20")],
        },
        "credits": [("Lenny Marsh", "Tenor saxophone"), ("Ruth Okafor", "Piano"), ("Dino Castellano", "Double bass"),
                    ("Walt Pryor", "Drums"), ("Harold Lime", "Recording engineer")],
        "bio": "The Lenny Marsh Quartet recorded Blue Hour Sessions in a single night in a converted Brooklyn "
               "church, with the lights turned low and no second takes.",
    },
    {
        "id": "prisms", "title": "Kaleidoscope Garden", "artist": "The Velvet Prisms", "year": "1967",
        "genre": "Psychedelic Rock", "label": "Paisley", "catno": "PL 1167", "medium": "vinyl",
        "sides": {
            "A": [("Kaleidoscope Garden", "3:41"), ("Mrs. Hollow's Umbrella", "2:58"), ("Sugar Cube Sky", "4:12"),
                  ("Tangerine Machine", "3:30")],
            "B": [("Rain on the Hourglass", "5:02"), ("Paper Moon Parade", "3:15"), ("Down the Rabbit Lane", "6:47")],
        },
        "credits": [("Nigel Ashby", "Vocals, rhythm guitar"), ("Pete Faraday", "Lead guitar, sitar"),
                    ("Colin Brightwater", "Bass, organ"), ("Mick Tolley", "Drums"), ("Ian Severs", "Producer")],
        "bio": "Four art students from Leeds, The Velvet Prisms made one album before splitting up in 1968. "
               "Kaleidoscope Garden has since become a collector's favorite.",
    },
    {
        "id": "dellarae", "title": "Sunday Morning Fire", "artist": "Della Rae & The Southern Lights", "year": "1972",
        "genre": "Soul", "label": "Gold Coast", "catno": "GC 2207", "medium": "vinyl",
        "sides": {
            "A": [("Sunday Morning Fire", "4:21"), ("Keep the Porch Light On", "3:52"), ("Sweet Magnolia Rain", "4:05"),
                  ("Halfway to Memphis", "3:38"), ("Hold On, Little Sister", "4:40")],
            "B": [("Brand New Shoes", "3:14"), ("Riverside Prayer", "5:11"), ("You Don't Have to Say", "3:47"),
                  ("Lay It Down Easy", "4:02"), ("Morning Comes Around", "5:26")],
        },
        "credits": [("Della Rae Simmons", "Lead vocals"), ("Otis Granger", "Guitar"), ("Bernice Wells", "Backing vocals"),
                    ("Leroy Dunn", "Bass"), ("Carl \"Skip\" Hampton", "Drums"), ("The Gold Coast Horns", "Horns"),
                    ("Marcus Bell", "Producer"), ("Ida Fontaine", "Arranger")],
        "bio": "Della Rae Simmons sang in her father's church in Macon, Georgia, before Gold Coast signed her and "
               "her band in 1970. Sunday Morning Fire, their second album, was recorded live in three days, and "
               "its title song stayed on soul radio for most of 1972.",
        "lyrics_song": "Keep the Porch Light On",
        "lyrics": [
            (0.0, "Down the county road the evening's falling slow"),
            (5.5, "I can see your window glowing from below"),
            (11.0, "Every mile I'm gone, I hold on to that glow"),
            (16.5, ""),
            (18.0, "So keep the porch light on"),
            (22.0, "Keep the porch light on"),
            (26.0, "When the night gets long"),
            (30.0, "Keep the porch light on for me"),
            (35.0, ""),
            (37.0, "Tell the lonesome dog I'll be home before the dawn"),
            (42.5, "Leave the screen door open and the radio on"),
        ],
    },
    {
        "id": "highway", "title": "Highway Dust", "artist": "Cal Whitmore", "year": "1975",
        "genre": "Country", "label": "Tumbleweed", "catno": "TW-508", "medium": "vinyl",
        "sides": {
            "A": [("Highway Dust", "3:22"), ("Amarillo Morning", "3:05"), ("Two-Lane Heart", "2:58"), ("Silver Buckle", "3:40")],
            "B": [("Last Gas for Ninety Miles", "4:01"), ("Mama's Kitchen Radio", "3:17"), ("Out Past Lubbock", "4:28")],
        },
        "credits": [("Cal Whitmore", "Vocals, acoustic guitar"), ("June Haskins", "Pedal steel"), ("Rusty Coombs", "Fiddle"),
                    ("Dale Pruitt", "Producer")],
        "bio": "Cal Whitmore drove a truck across West Texas for ten years and wrote these songs on the road.",
    },
    {
        "id": "mirrorball", "title": "Neon Avenue", "artist": "Mirrorball Society", "year": "1979",
        "genre": "Disco", "label": "Starfield", "catno": "SF 7900", "medium": "vinyl",
        "sides": {
            "A": [("Neon Avenue", "6:15"), ("Satin Nights", "5:40"), ("Dance Floor Galaxy", "7:02")],
            "B": [("Love Is a Rhythm", "5:55"), ("Midnight Taxi", "4:48"), ("Glitter Rain", "8:10")],
        },
        "credits": [("Gloria Vance", "Vocals"), ("Tony Bellucci", "Keyboards, producer"), ("Ray Simmons", "Bass")],
        "bio": "A New York studio project that became a real band after Neon Avenue filled the clubs in 1979.",
    },
    {
        "id": "satellites", "title": "Signal & Noise", "artist": "Paper Satellites", "year": "1983",
        "genre": "New Wave", "label": "Grid", "catno": "GRID 014", "medium": "cassette",
        "sides": {
            "A": [("Signal & Noise", "4:02"), ("Glass Telephone", "3:47"), ("Modern Weather", "4:20"), ("Static Heart", "3:55")],
            "B": [("Polaroid Summer", "4:11"), ("Satellite Love", "3:39"), ("Tomorrow Is a Machine", "5:08")],
        },
        "credits": [("Simon Clarke", "Vocals, synthesizers"), ("Anya Petrova", "Synthesizers"), ("Gavin Holt", "Drum machine")],
        "bio": "Paper Satellites built their sound from secondhand synthesizers in a Manchester bedsit.",
    },
    {
        "id": "concrete", "title": "Concrete Poetry", "artist": "Lorenzo Vega", "year": "1994",
        "genre": "Hip Hop", "label": "Fifth Borough", "catno": "FB-2294", "medium": "cd",
        "sides": {
            "1": [("Intro (Morning Train)", "1:12"), ("Concrete Poetry", "4:01"), ("Corner Store Philosophy", "3:48"),
                  ("Fire Escape", "4:15"), ("Bodega Lights", "3:33"), ("Notebook", "4:22"), ("Outro (Night Train)", "2:05")],
        },
        "credits": [("Lorenzo Vega", "Vocals"), ("DJ Paz", "Producer, turntables")],
        "bio": "Lorenzo Vega's debut, written on the subway between shifts.",
    },
    {
        "id": "pines", "title": "Small Hours", "artist": "Hollow Pines", "year": "2009",
        "genre": "Indie Folk", "label": "Driftwood", "catno": "DW-031", "medium": "vinyl",
        "sides": {
            "A": [("Small Hours", "3:58"), ("Woodsmoke", "4:20"), ("Cold Lake", "3:41"), ("Lanterns", "4:55")],
            "B": [("Thaw", "4:02"), ("The Long Way North", "5:30"), ("Kindling", "3:12"), ("Home by Morning", "6:01")],
        },
        "credits": [("Maren Holt", "Vocals, guitar"), ("Eli Brandt", "Banjo, harmonium"), ("Sam Whitlock", "Recording")],
        "bio": "Recorded in a cabin in northern Minnesota over one snowed-in winter.",
    },
]

# "More like this" for the soul album: other made-up acts, a few of them already on the shelf
SIMILAR = ["Otis Granger Band", "The Magnolia Sisters", "Jimmy Lee Carver", "Sweet Tea Revue", "Marcus Bell Orchestra",
           "Velma Hayes", "The Riverside Five", "Booker Lane"]


def by_id(i):
    return next(a for a in ALBUMS if a["id"] == i)

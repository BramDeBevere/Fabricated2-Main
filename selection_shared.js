/*
  selection_shared.js
  ───────────────────
  Single source of truth for the installation's "rabbit hole" content and
  navigation logic. Extracted verbatim from installation.html so that the
  original experience, Projector 2, and the tablet controls all REUSE the
  same trees / rewards / selection images / walker instead of each copying it.

  Exposes (as globals, matching installation.html's original names):
    NATURE_TREE, CULTURE_TREE, POLITICS_TREE, HISTORY_TREE,
    TOPIC_TREES, REWARDS, SEL_IMAGES

  And a reusable, framework-agnostic tree walker as `window.SELECTION`:
    SELECTION.newWalkerState()   -> a fresh walker state
    SELECTION.isInTopicBracket(s)
    SELECTION.optionsOf(s)       -> [leftLabel, rightLabel]
    SELECTION.applyChoice(s, side) -> advances; returns the leaf if finished
    SELECTION.back(s)            -> undo the last choice (returns bool)
    SELECTION.restart(s)         -> reset to the topic bracket
*/

// Tree-walker: each node is { left, right, children? }
// A node without children is a leaf — the chosen side is the final answer.

const NATURE_TREE = {
  left: 'Moving', right: 'Still',
  children: {
    'Moving': {
      left: 'Water', right: 'Sky',
      children: {
        'Water': {
          left: 'Water', right: 'Land',
          children: {
            'Water': { left: 'Fish',         right: 'Shark'   },
            'Land':  { left: 'Insect',       right: 'Monkey'  },
          },
        },
        'Sky': {
          left: 'Sky', right: 'Land',
          children: {
            'Sky':  { left: 'Flying Insect', right: 'Bird'    },
            'Land': { left: 'Insect',        right: 'Monkey'  },
          },
        },
      },
    },
    'Still': {
      left: 'Big', right: 'Small',
      children: {
        'Big':   { left: 'Mountain', right: 'Waterfall' },
        'Small': { left: 'Tree',     right: 'Flower'    },
      },
    },
  },
};

const CULTURE_TREE = {
  left: 'Arts', right: 'Lifestyle',
  children: {
    'Arts': {
      left: 'Music', right: 'Dance',
      children: {
        'Music': {
          left: 'Traditional', right: 'Modern',
          children: {
            'Traditional': { left: 'Joropo',             right: 'Gaita'                  },
            'Modern':      { left: 'Salsa',              right: 'Urban Venezuelan Rap'   },
          },
        },
        'Dance': {
          left: 'Traditional', right: 'Festive',
          children: {
            'Traditional': { left: 'Joropo dancing',     right: 'Tamunangue'             },
            'Festive':     { left: 'El Callao Carnival', right: 'Caracas street dance'   },
          },
        },
      },
    },
    'Lifestyle': {
      left: 'Food', right: 'Sport',
      children: {
        'Food': {
          left: 'Street food', right: 'Festive dishes',
          children: {
            'Street food':    { left: 'Arepas',                  right: 'Cachapas'           },
            'Festive dishes': { left: 'Hallacas',                right: 'Pabellón Criollo'   },
          },
        },
        'Sport': {
          left: 'Baseball', right: 'Other sports',
          children: {
            'Baseball':     { left: 'Venezuelan MLB players', right: 'Local stadiums' },
            'Other sports': { left: 'Cycling',               right: 'Tejo'           },
          },
        },
      },
    },
  },
};

const POLITICS_TREE = {
  left: 'Internal Dynamics', right: 'External & Social Impact',
  children: {
    'Internal Dynamics': {
      left: 'Crisis', right: 'Resistance',
      children: {
        'Crisis': {
          left: 'Displacement', right: 'Economy',
          children: {
            'Displacement': { left: 'Border crossings',         right: 'Diaspora stories'   },
            'Economy':      { left: 'Hyperinflation',           right: 'Empty shelves'      },
          },
        },
        'Resistance': {
          left: 'Protest', right: 'Digital Space',
          children: {
            'Protest':       { left: '2024 election protests',  right: 'Student movements'  },
            'Digital Space': { left: 'Social media censorship', right: 'TikTok ban'         },
          },
        },
      },
    },
    'External & Social Impact': {
      left: 'International', right: 'Society',
      children: {
        'International': {
          left: 'Aid', right: 'Sanctions',
          children: {
            'Aid':       { left: 'UN relief efforts',     right: 'UNHCR camps'          },
            'Sanctions': { left: 'US-Venezuela standoff', right: 'Oil embargo'          },
          },
        },
        'Society': {
          left: 'Everyday Survival', right: 'Daily Life',
          children: {
            'Everyday Survival': { left: 'Shortages',        right: 'Healthcare collapse' },
            'Daily Life':        { left: 'Education crisis', right: 'Lost generation'     },
          },
        },
      },
    },
  },
};

const HISTORY_TREE = {
  left: 'The Past', right: 'The Present & Future',
  children: {
    'The Past': {
      left: 'Early Heritage', right: 'Colonial Era',
      children: {
        'Early Heritage': {
          left: 'Indigenous', right: 'Pre-colonial',
          children: {
            'Indigenous':   { left: 'Pemon People',   right: 'Warao canoes'          },
            'Pre-colonial': { left: 'Tepui legends',  right: 'Orinoco civilizations' },
          },
        },
        'Colonial Era': {
          left: 'Spanish', right: 'Resistance',
          children: {
            'Spanish':    { left: 'Caracas founding', right: 'Cathedral'        },
            'Resistance': { left: 'Simon Bolivar',    right: 'Independence war' },
          },
        },
      },
    },
    'The Present & Future': {
      left: 'Oil Era', right: 'Modern',
      children: {
        'Oil Era': {
          left: 'Boom', right: 'Decline',
          children: {
            'Boom':    { left: '1970s wealth',   right: 'Caracas skyline' },
            'Decline': { left: 'PDVSA collapse', right: '2000s poverty'   },
          },
        },
        'Modern': {
          left: 'Hope', right: 'Diaspora',
          children: {
            'Hope':     { left: 'Return migration 2025', right: 'Community rebuilding'     },
            'Diaspora': { left: 'Venezuelans worldwide', right: 'Cultural identity abroad' },
          },
        },
      },
    },
  },
};

const TOPIC_TREES = { Nature: NATURE_TREE, Culture: CULTURE_TREE, Politics: POLITICS_TREE, History: HISTORY_TREE };

// ── Reward content — replace src and text with real content when ready ────────
// type: 'image' | 'video'
const REWARDS = {
  // ── Nature ──────────────────────────────────────────────────────────────────
'Fish':           { type: 'image', src: 'nature_images/fish.jpg', text: 'Did you know? The mighty Orinoco River in Venezuela is home to over 1,000 species of fish, including the fierce red-bellied piranha and the colorful Pavón (Peacock Bass).' },
  'Shark':          { type: 'image', src: 'nature_images/shark.jpg', text: 'Venezuela\'s stunning Los Roques archipelago in the Caribbean Sea serves as a critical, crystal-clear nursery for several species of beautiful reef sharks.' },
  'Flying Insect':  { type: 'image', src: 'nature_images/flying-insect.jpg', text: 'The brilliant Blue Morpho butterfly flashes its iridescent wings across the Venezuelan Andes, creating a dazzling, glowing sight amidst the misty cloud forests.' },
  'Bird':           { type: 'image', src: 'nature_images/bird.jpg', text: 'Venezuela\'s proud national bird is the Turpial (Troupial). It is beloved for its striking yellow-orange and black plumage, and its beautiful, highly melodic song.' },
  'Insect':         { type: 'image', src: 'nature_images/insect.jpg', text: 'Venezuela\'s rich rainforests are home to the incredible Hercules beetle, one of the largest flying insects in the world, capable of carrying 850 times its own body weight!' },
  'Monkey':         { type: 'image', src: 'nature_images/cute-monkey.jpg', text: 'Deep in the Venezuelan canopy lives the Venezuelan Red Howler monkey. Their magnificent territorial calls are so loud they can be heard up to 3 miles away in the dense jungle.' },
  'Mountain':       { type: 'image', src: 'nature_images/mountain.jpg', text: 'Venezuela boasts ancient, majestic flat-topped mountains called \'Tepuis\'. Mount Roraima is so unique and isolated that it famously inspired Arthur Conan Doyle\'s novel \'The Lost World\'.' },
  'Waterfall':      { type: 'image', src: 'nature_images/waterfall.jpg', text: 'A true wonder of the world: Venezuela is home to Angel Falls (Salto Ángel), the highest uninterrupted waterfall on Earth, plunging a staggering 979 meters (3,212 ft) from the edge of Auyán-tepui.' },
  'Tree':           { type: 'image', src: 'nature_images/tree.jpg', text: 'Every creature, every leaf, every current of water tells a story. You chose your path through nature — and nature chose to show you something in return.' },
  'Flower':         { type: 'image', src: 'nature_images/flower.jpg', text: 'The \'Flor de Mayo\' (Cattleya mossiae), an exquisite and deeply fragrant orchid native to the country, was proudly declared the national flower of Venezuela in 1951.' },
  // ── Culture ─────────────────────────────────────────────────────────────────
  'Joropo':                  { type: 'image', src: '', text: 'Joropo is Venezuela\'s national music — born on the open plains, its harp and cuatro carry the soul of the llanos.' },
  'Gaita':                   { type: 'image', src: '', text: 'Gaita from Maracaibo is the heartbeat of Venezuelan Christmas, its rhythms inseparable from the holiday season.' },
  'Salsa':                   { type: 'image', src: '', text: 'Salsa flows through Caracas — from neighbourhood parties to concert halls, it is the language of movement here.' },
  'Urban Venezuelan Rap':    { type: 'image', src: '', text: 'Venezuelan hip-hop emerged in the 1990s as a voice for urban youth, speaking of inequality, identity, and survival.' },
  'Joropo dancing':          { type: 'image', src: '', text: 'Joropo dance is performed barefoot on wooden floors — rapid footwork, spinning partners, and deep roots in the plains.' },
  'Tamunangue':              { type: 'image', src: '', text: 'Tamunangue is an Afro-Venezuelan ceremonial dance from Lara, blending African, Indigenous, and Spanish traditions.' },
  'El Callao Carnival':      { type: 'image', src: '', text: 'El Callao Carnival is a UNESCO World Heritage celebration — two weeks of music, costume, and Afro-Caribbean joy.' },
  'Caracas street dance':    { type: 'image', src: '', text: 'Caracas streets have always been a stage — from breakdancing to contemporary styles, the city moves.' },
  'Arepas':                  { type: 'image', src: '', text: 'The arepa is Venezuela\'s daily bread — a grilled corn flatbread eaten morning, noon, and night in countless forms.' },
  'Cachapas':                { type: 'image', src: '', text: 'Cachapas are thick sweet corn pancakes folded over white cheese — a beloved roadside and family staple.' },
  'Hallacas':                { type: 'image', src: '', text: 'Hallacas — corn dough filled with stew, wrapped in plantain leaves — take days to make and are eaten at Christmas.' },
  'Pabellón Criollo':        { type: 'image', src: '', text: 'Pabellón Criollo is Venezuela\'s national dish: rice, black beans, shredded beef, and sweet fried plantain.' },
  'Venezuelan MLB players':  { type: 'image', src: '', text: 'Venezuela has sent more players per capita to Major League Baseball than any other country in the world.' },
  'Local stadiums':          { type: 'image', src: '', text: 'Baseball stadiums are the heart of Venezuelan communities — packed on weekends, loud with life.' },
  'Cycling':                 { type: 'image', src: '', text: 'The Tour del Táchira, held each January, is one of Latin America\'s most prestigious and gruelling cycling races.' },
  'Tejo':                    { type: 'image', src: '', text: 'Tejo is a traditional throwing sport played across Venezuela and Colombia, where metal discs detonate gunpowder targets.' },
  // ── Politics ────────────────────────────────────────────────────────────────
  'Border crossings':        { type: 'image', src: '', text: 'Rising migration pressures are pushing more people to cross borders, often risking dangerous routes in search of safety or opportunity.' },
  'Diaspora stories':        { type: 'image', src: '', text: 'Communities living abroad preserve culture while adapting to new societies, shaping identity across generations.' },
  'Hyperinflation':          { type: 'image', src: '', text: 'Rapid price increases erode purchasing power, making basic goods unaffordable and destabilizing economies.' },
  'Empty shelves':           { type: 'image', src: '', text: 'Supply chain disruptions and economic crises can lead to shortages of essential goods in stores.' },
  '2024 election protests':  { type: 'image', src: '', text: 'Political tensions around elections have sparked demonstrations, reflecting deep divisions and demands for transparency.' },
  'Student movements':       { type: 'image', src: '', text: 'Young people are mobilizing globally, advocating for political reform, climate action, and social justice.' },
  'Social media censorship': { type: 'image', src: '', text: 'Governments and platforms face criticism over restricting content, raising concerns about free speech.' },
  'TikTok ban':              { type: 'image', src: '', text: 'Debates over data privacy and national security have led some countries to consider or enforce bans.' },
  'UN relief efforts':       { type: 'image', src: '', text: 'International aid organizations provide food, shelter, and medical support in crisis zones worldwide.' },
  'UNHCR camps':             { type: 'image', src: '', text: 'Refugee camps offer temporary protection but often struggle with overcrowding and limited resources.' },
  'US-Venezuela standoff':   { type: 'image', src: '', text: 'Political and economic tensions continue to shape relations, affecting sanctions and diplomacy.' },
  'Oil embargo':             { type: 'image', src: '', text: 'Restrictions on oil exports can disrupt global markets, driving up energy prices.' },
  'Shortages':               { type: 'image', src: '', text: 'Lack of fuel, food, or medicine highlights systemic economic and logistical challenges.' },
  'Healthcare collapse':     { type: 'image', src: '', text: 'Underfunded systems and crises can overwhelm hospitals, limiting access to care.' },
  'Education crisis':        { type: 'image', src: '', text: 'School disruptions and underinvestment threaten learning outcomes for entire generations.' },
  'Lost generation':         { type: 'image', src: '', text: 'Prolonged instability can leave young people without opportunities, impacting long-term development.' },
  // ── History ─────────────────────────────────────────────────────────────────
  'Pemon People':            { type: 'image', src: '', text: 'The Pemon are the Indigenous people of the Gran Sabana — keepers of the tepui landscape and its ancient stories.' },
  'Warao canoes':            { type: 'image', src: '', text: 'The Warao of the Orinoco Delta have navigated its waterways for thousands of years, their dugout canoes still cutting the current.' },
  'Tepui legends':           { type: 'image', src: '', text: 'The tepuis — ancient flat-topped mountains — are sacred in Indigenous cosmology, home to spirits and the origin of the world.' },
  'Orinoco civilizations':   { type: 'image', src: '', text: 'Along the Orinoco River, complex pre-colonial societies flourished for millennia before European contact.' },
  'Caracas founding':        { type: 'image', src: '', text: 'Caracas was founded by the Spanish in 1567. From a colonial outpost, it grew into a capital of four million.' },
  'Cathedral':               { type: 'image', src: '', text: 'Caracas Cathedral, begun in 1665, stands at the historic heart of the city — a stone witness to centuries of change.' },
  'Simon Bolivar':           { type: 'image', src: '', text: 'Simón Bolívar freed six nations from Spanish rule. Born in Caracas, he remains the defining figure of Venezuelan identity.' },
  'Independence war':        { type: 'image', src: '', text: 'Venezuela\'s war of independence (1810–1823) was brutal and transformative — reshaping a continent.' },
  '1970s wealth':            { type: 'image', src: '', text: 'Oil booms in the 1970s made Venezuela one of the wealthiest nations in Latin America. Caracas gleamed.' },
  'Caracas skyline':         { type: 'image', src: '', text: 'The Caracas skyline of the 1970s told a story of prosperity — glass towers, wide highways, and middle-class hope.' },
  'PDVSA collapse':          { type: 'image', src: '', text: 'PDVSA, Venezuela\'s state oil company, once drove the nation\'s wealth. Its collapse took the economy with it.' },
  '2000s poverty':           { type: 'image', src: '', text: 'By the 2000s, the oil wealth had faded for most. Poverty deepened while political tension rose.' },
  'Return migration 2025':   { type: 'image', src: '', text: 'Some Venezuelans are returning — cautiously, hopefully — building again in a country still finding its footing.' },
  'Community rebuilding':    { type: 'image', src: '', text: 'Across Venezuela, neighbours organise, community kitchens open, and people rebuild what institutions abandoned.' },
  'Venezuelans worldwide':   { type: 'image', src: '', text: 'From Madrid to Lima to New York — Venezuelans have built new lives abroad while carrying their homeland with them.' },
  'Cultural identity abroad':{ type: 'image', src: '', text: 'In diaspora, Venezuelan identity grows stronger: the food, the music, the accent — all become acts of memory.' },
};

// ── Selection images — replace '' with real paths when ready ──────────────────
const SEL_IMAGES = {
  // Topic bracket
  'Nature':                     'nature_images/nature.jpg',
  'Culture':                    'culture_images/dance.jpg',
  'Politics':                   'politics_images/politics.jpg',
  'History':                    'history_images/history.jpg',
  // Nature
  'Moving':                     'nature_images/moving.jpg',
  'Still':                      'nature_images/still.jpg',
  'Water':                      'nature_images/water.jpg',
  'Sky':                        'nature_images/sky.jpg',
  'Land':                       'nature_images/land.jpg',
  'Big':                        'nature_images/big.jpg',
  'Small':                      'nature_images/small.jpg',
  'Fish':                       'nature_images/fish.jpg',
  'Shark':                      'nature_images/shark.jpg',
  'Flying Insect':              'nature_images/flying-insect.jpg',
  'Bird':                       'nature_images/bird.jpg',
  'Insect':                     'nature_images/insect.jpg',
  'Monkey':                     'nature_images/cute-monkey.jpg',
  'Mountain':                   'nature_images/mountain.jpg',
  'Waterfall':                  'nature_images/waterfall.jpg',
  'Tree':                       'nature_images/tree.jpg',
  'Flower':                     'nature_images/flower.jpg',
  // Culture
  'Arts':                       'culture_images/arts.jpg',
  'Lifestyle':                  'culture_images/lifestyle .jpg',
  'Music':                      'culture_images/music.jpg',
  'Dance':                      'culture_images/dance.jpg',
  'Food':                       'culture_images/food.jpg',
  'Sport':                      'culture_images/sport.jpg',
  'Traditional':                'culture_images/Dance traditional.jpeg',
  'Modern':                     'culture_images/music modern.jpeg',
  'Festive':                    'culture_images/festive dance.jpeg',
  'Street food':                'culture_images/street food.jpeg',
  'Festive dishes':             'culture_images/festive food.jpeg',
  'Baseball':                   'culture_images/sport baseball .jpg',
  'Other sports':               'culture_images/other sport .jpeg',
  'Joropo':                     'culture_images/joropo.jpg',
  'Gaita':                      'culture_images/gaita.jpg',
  'Salsa':                      'culture_images/salsa.jpg',
  'Urban Venezuelan Rap':       'culture_images/urban-venezuelan-rap.jpg',
  'Joropo dancing':             'culture_images/joropo-dancing.jpg',
  'Tamunangue':                 'culture_images/tamunangue.jpg',
  'El Callao Carnival':         'culture_images/el-callao-carnival.jpg',
  'Caracas street dance':       'culture_images/caracas-street-dance.jpg',
  'Arepas':                     'culture_images/street food arepas.jpg',
  'Cachapas':                   'culture_images/street food cachapa .jpg',
  'Hallacas':                   'culture_images/hallacas  festive food.jpg',
  'Pabellón Criollo':           'culture_images/festive food pabéllon criollo.jpg',
  'Venezuelan MLB players':     'culture_images/venezuelan-mlb-players.jpg',
  'Local stadiums':             'culture_images/sport local stadium.jpg',
  'Cycling':                    'culture_images/other cycling .jpeg',
  'Tejo':                       'culture_images/tejo.jpg',
  // Politics
  'Internal Dynamics':          'politics_images/',
  'External & Social Impact':   'politics_images/',
  'Crisis':                     'politics_images/Crisis.jpeg',
  'Resistance':                 'politics_images/Resistance.jpg',
  'International':              'politics_images/international.jpg',
  'Society':                    'politics_images/society.jpeg',
  'Displacement':               'politics_images/displacement.png',
  'Economy':                    'politics_images/economy.jpg',
  'Protest':                    'politics_images/',
  'Digital Space':              'politics_images/digital.jpeg',
  'Aid':                        'politics_images/aid.jpg',
  'Sanctions':                  'politics_images/sanctions.jpg',
  'Everyday Survival':          'politics_images/everyday_society.jpeg',
  'Daily Life':                 'politics_images/',
  'Border crossings':           'politics_images/Border_crossings.jpg',
  'Diaspora stories':           'politics_images/dispora.png',
  'Hyperinflation':             'politics_images/Hyperinflation.avif',
  'Empty shelves':              'politics_images/',
  '2024 election protests':     'politics_images/2024_election_protests.jpg',
  'Student movements':          'politics_images/Student_movements.jpg',
  'Social media censorship':    'politics_images/Social_media_censorship.jpg',
  'TikTok ban':                 'politics_images/TikTok_ban.jpg',
  'UN relief efforts':          'politics_images/UN_relief_efforts.jpg',
  'UNHCR camps':                'politics_images/UNHCR_camps.jpg',
  'US-Venezuela standoff':      'politics_images/US_Venezuela_standoff.jpg',
  'Oil embargo':                'politics_images/oil_embargo .jpg',
  'Shortages':                  'politics_images/Shortages .jpg',
  'Healthcare collapse':        'politics_images/healthcare_collapse.jpg',
  'Education crisis':           'politics_images/Education_crisis.jpg',
  'Lost generation':            'politics_images/Lost_generation.jpg',
  // History
  'The Past':                   'history_images/ancient2.jpeg',
  'The Present & Future':       'history_images/modern2.jpeg',
  'Early Heritage':             'history_images/',
  'Colonial Era':               'history_images/Colonial1.jpeg',
  'Oil Era':                    'history_images/oil-era.jpg',
  'Modern':                     'history_images/modern.jpeg',
  'Indigenous':                 'history_images/indigenous.jpg',
  'Pre-colonial':               'history_images/pre-colonial.jpg',
  'Spanish':                    'history_images/spanish.jpg',
  'Boom':                       'history_images/',
  'Decline':                    'history_images/',
  'Hope':                       'history_images/',
  'Diaspora':                   'history_images/',
  'Pemon People':               'history_images/pemon-people.jpg',
  'Warao canoes':               'history_images/warao-canoes.jpg',
  'Tepui legends':              'history_images/tepui-legends.jpg',
  'Orinoco civilizations':      'history_images/orinoco-civilizations.jpg',
  'Caracas founding':           'history_images/caracas-founding.jpg',
  'Cathedral':                  'history_images/cathedral.jpg',
  'Simon Bolivar':              'history_images/simon-bolivar.jpg',
  'Independence war':           'history_images/independence-war.jpg',
  '1970s wealth':               'history_images/1970s-wealth.jpg',
  'Caracas skyline':            'history_images/caracas-skyline.jpg',
  'PDVSA collapse':             'history_images/pdvsa-collapse.jpg',
  '2000s poverty':              'history_images/2000s-poverty.jpg',
  'Return migration 2025':      'history_images/return-migration.jpg',
  'Community rebuilding':       'history_images/community-rebuilding.jpg',
  'Venezuelans worldwide':      'history_images/venezuelans-worldwide.jpg',
  'Cultural identity abroad':   'history_images/cultural-identity.jpg',
};



// ── Reusable tree walker ─────────────────────────────────────────────────────
// A plain, framework-agnostic state machine that mirrors the original
// installation.html selection flow: a 3-round topic bracket (Nature/Culture,
// then Politics/History, then the two winners), followed by the chosen topic's
// decision tree down to a leaf ("rabbit hole"). It keeps a small history stack
// so the tablet UI can support Back / Restart navigation.
(function (global) {
  'use strict';

  function newWalkerState() {
    return {
      topicPhase: 0,
      topicWinners: [],
      node: null,      // current tree node (null = still in the topic bracket)
      path: [],        // breadcrumb of committed choices
      round: 0,
      finished: false,
      choice: null,
      history: [],
    };
  }

  function isInTopicBracket(s) { return s.node === null; }

  function optionsOf(s) {
    if (isInTopicBracket(s)) {
      if (s.topicPhase === 0) return ['Nature', 'Culture'];
      if (s.topicPhase === 1) return ['Politics', 'History'];
      return [s.topicWinners[0], s.topicWinners[1]];
    }
    return [s.node.left, s.node.right];
  }

  // Commit a side choice. Returns the leaf name when the walk finishes, else null.
  function applyChoice(s, side) {
    var opts = optionsOf(s);
    var winner = (side === 'left') ? opts[0] : opts[1];
    s.history.push({
      topicPhase: s.topicPhase,
      topicWinners: s.topicWinners.slice(),
      node: s.node,
      path: s.path.slice(),
      round: s.round,
    });
    if (isInTopicBracket(s)) {
      if (s.topicPhase < 2) {
        s.topicWinners.push(winner);
        s.topicPhase++;
      } else {
        s.node = TOPIC_TREES[winner];
        s.path = [winner];
        s.round = 1;
      }
    } else {
      s.path.push(winner);
      var child = s.node.children && s.node.children[winner];
      if (child) {
        s.node = child;
        s.round++;
      } else {
        s.choice = winner;
        s.finished = true;
      }
    }
    return s.finished ? s.choice : null;
  }

  function back(s) {
    if (!s.history.length) return false;
    var prev = s.history.pop();
    s.topicPhase   = prev.topicPhase;
    s.topicWinners = prev.topicWinners;
    s.node         = prev.node;
    s.path         = prev.path;
    s.round        = prev.round;
    s.finished     = false;
    s.choice       = null;
    return true;
  }

  function restart(s) {
    var f = newWalkerState();
    for (var k in f) s[k] = f[k];
    return s;
  }

  global.SELECTION = {
    newWalkerState: newWalkerState,
    isInTopicBracket: isInTopicBracket,
    optionsOf: optionsOf,
    applyChoice: applyChoice,
    back: back,
    restart: restart,
    TOPIC_TREES: TOPIC_TREES,
    REWARDS: REWARDS,
    SEL_IMAGES: SEL_IMAGES,
  };
})(typeof window !== 'undefined' ? window : this);
